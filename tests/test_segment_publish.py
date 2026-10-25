"""Sealed segments: the synced folder never holds a file that is still growing.

These tests fail if a writer appends to a record file inside the graph folder.
That is the rclone bisync failure ``can't copy - source file is being updated
(size changed from 637 to 956)``.
"""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path

from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.paths import segment_seq
from graph_ted_db.store.records import NodeRecord
from graph_ted_db.store.segments import PublishPolicy, publish_immutable

# No time-based seal during the test. Size and explicit publish() only.
_HOLD = PublishPolicy(max_bytes=0, max_interval=0, idle=0, compact_after=0)


def _open(root: Path, data: Path, policy: PublishPolicy = _HOLD) -> GraphStore:
    return GraphStore.open(root, data_dir=data, publish=policy)


def _record_files(root: Path) -> dict[Path, int]:
    found: dict[Path, int] = {}
    for directory in (root / "nodes", root / "edges", root / "meta"):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.jsonl"):
            if path.is_file() and not path.name.endswith(".tmp"):
                found[path] = path.stat().st_size
    return found


def test_appends_do_not_change_files_already_in_the_synced_folder(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "d")
    first = store.make_node(labels=["E"], props={"i": 0})
    store.publish()
    before = _record_files(root)
    assert before, "the first seal should have published a segment"
    for i in range(40):
        store.make_node(labels=["E"], props={"i": i + 1, "blob": "x" * 40})
    after = _record_files(root)
    # New names may be absent. Every name that was already there keeps its size.
    assert {path: after[path] for path in before} == before
    assert store.get_node(first.id) is not None
    # The new bytes are local, not in a file a sync client would be copying.
    active_logs = [path for path in store._active_root.rglob("*.jsonl") if path.stat().st_size > 0]
    assert active_logs
    assert all(not path.resolve().is_relative_to(root.resolve()) for path in active_logs)


def test_fake_sync_never_sees_a_record_file_change_size(tmp_path: Path) -> None:
    """Stat, read, stat. A size change is the bisync 'source file is being updated' error."""
    root = tmp_path / "g"
    init_graph(root)
    policy = PublishPolicy(max_bytes=2048, max_interval=0, idle=0, compact_after=0)
    store = _open(root, tmp_path / "d", policy)
    errors: list[tuple[str, int, int, int]] = []
    stop = threading.Event()

    def copier() -> None:
        while not stop.is_set():
            for path in list((root / "nodes").glob("*.jsonl")):
                try:
                    size1 = path.stat().st_size
                    with path.open("rb") as handle:
                        data = handle.read()
                    size2 = path.stat().st_size
                except FileNotFoundError:
                    continue
                if size1 != size2 or len(data) != size1:
                    errors.append((path.name, size1, size2, len(data)))
                    stop.set()

    thread = threading.Thread(target=copier)
    thread.start()
    try:
        for i in range(80):
            store.make_node(labels=["E"], props={"i": i, "pad": "y" * 60})
        store.publish()
    finally:
        stop.set()
        thread.join()
    assert errors == []
    assert list((root / "nodes").glob(f"{store.writer_id}.*.jsonl"))


def test_size_trigger_seals_without_an_explicit_publish(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(
        root,
        tmp_path / "d",
        PublishPolicy(max_bytes=300, max_interval=0, idle=0, compact_after=0),
    )
    store.make_node(labels=["E"], props={"i": 0, "pad": "z" * 400})
    sealed = list((root / "nodes").glob(f"{store.writer_id}.*.jsonl"))
    assert len(sealed) == 1
    size = sealed[0].stat().st_size
    store.make_node(labels=["E"], props={"i": 1})
    assert sealed[0].stat().st_size == size


def test_v2_file_is_sealed_on_open_and_then_left_alone(tmp_path: Path) -> None:
    root = tmp_path / "g"
    meta = init_graph(root)
    raw = meta.to_dict()
    raw["format_version"] = 2
    raw["layout"] = "per-writer"
    (root / "graph.json").write_text(__import__("json").dumps(raw), encoding="utf-8")
    data = tmp_path / "d"
    writer = _open(root, data).writer_id
    legacy = root / "nodes" / f"0f.{writer}.jsonl"
    record = NodeRecord(
        id="0f000000-0000-4000-8000-000000000001",
        updated_at="2026-08-25T12:00:00.000000Z",
        labels=("E",),
        props={"name": "legacy"},
        writer=writer,
    )
    legacy.parent.mkdir(parents=True, exist_ok=True)
    payload = (record.to_jsonl() + "\n").encode()
    legacy.write_bytes(payload)

    reopened = _open(root, data)
    assert not legacy.exists()
    sealed = list((root / "nodes").glob(f"{writer}.*.jsonl"))
    assert len(sealed) == 1
    assert sealed[0].read_bytes() == payload
    assert reopened.get_node(record.id) is not None
    assert reopened.meta.format_version == 4
    assert reopened.meta.extras["layout"] == "bundles"
    before = sealed[0].read_bytes()
    reopened.make_node(labels=["E"], props={"name": "after"})
    assert sealed[0].read_bytes() == before
    report = reopened.doctor()
    assert report.legacy_v2_files == []
    assert f"nodes/{legacy.name}" in report.migrated


def test_reader_tolerates_legacy_and_segment_together(tmp_path: Path) -> None:
    """A crash between seal and delete leaves both names. Readers must not drop the id."""
    root = tmp_path / "g"
    init_graph(root)
    owner = _open(root, tmp_path / "owner")
    record = NodeRecord(
        id="0f000000-0000-4000-8000-000000000002",
        updated_at="2026-08-25T12:00:00.000000Z",
        labels=("E",),
        props={"name": "once"},
        writer=owner.writer_id,
    )
    line = (record.to_jsonl() + "\n").encode()
    legacy = root / "nodes" / f"0f.{owner.writer_id}.jsonl"
    sealed = root / "nodes" / f"0f.{owner.writer_id}.000001.jsonl"
    legacy.write_bytes(line)
    sealed.write_bytes(line)
    reader = _open(root, tmp_path / "reader")
    assert reader.get_node(record.id) is not None
    assert [node.props["name"] for node in reader.iter_nodes()] == ["once"]
    # The owner finishes the migration. The id is still there, and not twice.
    owner.close()
    migrated = _open(root, tmp_path / "owner")
    assert not legacy.exists()
    assert [node.props["name"] for node in migrated.iter_nodes()] == ["once"]


def test_two_writers_never_publish_the_same_name(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    init_graph(root_a)
    root_b = tmp_path / "b"
    root_b.mkdir()
    for path in root_a.rglob("*"):
        dest = root_b / path.relative_to(root_a)
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.write_bytes(path.read_bytes())
    writer_a = _open(root_a, tmp_path / "da")
    writer_b = _open(root_b, tmp_path / "db")
    for i in range(5):
        writer_a.make_node(labels=["E"], props={"by": "a", "i": i})
        writer_b.make_node(labels=["E"], props={"by": "b", "i": i})
    writer_a.publish()
    writer_b.publish()
    names_a = {path.name for path in (root_a / "nodes").glob("*.jsonl")}
    names_b = {path.name for path in (root_b / "nodes").glob("*.jsonl")}
    assert names_a and names_b
    assert names_a.isdisjoint(names_b)
    for name in names_a:
        assert writer_a.writer_id in name and writer_b.writer_id not in name


def test_one_publish_is_one_file_when_every_shard_is_touched(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "d")
    for shard in range(256):
        record = NodeRecord(
            id=f"{shard:02x}000000-0000-4000-8000-000000000001",
            updated_at="2026-08-25T12:00:00.000000Z",
            labels=("E",),
            props={"shard": shard},
        )
        store.put_node(record)
    assert store.publish() == 1
    files = list((root / "nodes").glob("*.jsonl"))
    assert [path.name for path in files] == [f"{store.writer_id}.000001.jsonl"]
    assert store.iter_nodes() and len(list(store.iter_nodes())) == 256


def test_large_burst_keeps_the_file_count_bounded(tmp_path: Path) -> None:
    """A burst that touches every shard must not create one file per shard.

    The per-shard layout sealed a new file for every shard in the batch and
    kept ``compact_after`` of them, so one writer could approach 256 × 16
    files. A bundle is one file per publish, and compaction keeps at most
    ``compact_after`` of this writer's files in the directory.
    """
    root = tmp_path / "g"
    init_graph(root)
    policy = PublishPolicy(max_bytes=32_768, max_interval=0, idle=0, compact_after=4)
    store = _open(root, tmp_path / "d", policy)
    for i in range(4_000):
        store.make_node(labels=["E"], props={"i": i, "pad": "x" * 24})
    store.publish()
    files = list((root / "nodes").glob(f"{store.writer_id}.*.jsonl"))
    seqs = [segment_seq(path.name) for path in files]
    assert seqs and None not in seqs
    assert len(files) <= policy.compact_after
    # Sequences are not reused, so the highest one is how many node files
    # this burst created. That stays far below one file per shard.
    assert max(seqs) < 256
    assert list((root / "nodes").glob(f"??.{store.writer_id}.*.jsonl")) == []
    assert len(list(store.iter_nodes())) == 4_000


def test_publish_across_filesystems_installs_the_final_bytes(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "nodes" / "00.w0123456789abcdef.000001.jsonl"
    stage = tmp_path / "stage"
    payload = b'{"id":"x"}\n'
    real = os.replace
    calls = {"n": 0}

    def wrapped(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", wrapped)
    publish_immutable(dest, payload, stage_dir=stage)
    assert dest.read_bytes() == payload
    assert list(dest.parent.glob("*.tmp")) == []
    assert list(stage.glob("*.tmp")) == []
