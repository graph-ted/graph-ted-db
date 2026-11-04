"""BUG-007: a writer puts back its own published files that a sync client deleted.

The publish ledger (local, never synced) records every file this writer sealed,
with a retained copy of its bytes. A missing live file is republished under the
same name with the same bytes, a name is never reused, and a file replaced by
compaction is never brought back.
"""

from __future__ import annotations

import logging
from pathlib import Path

from graph_ted_db.cli import main as cli_main
from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.segments import PublishPolicy

_HOLD = PublishPolicy(max_bytes=0, max_interval=0, idle=0, compact_after=0)


def _open(root: Path, data: Path) -> GraphStore:
    return GraphStore.open(root, data_dir=data, publish=_HOLD)


def _own_node_files(store: GraphStore) -> list[Path]:
    return sorted(p for p in (store.root / "nodes").rglob("*.jsonl") if store.writer_id in p.name)


def _write(store: GraphStore, n: int, tag: str) -> list[str]:
    ids = [store.make_node(labels=["E"], props={"tag": tag, "i": i}).id for i in range(n)]
    store.publish()
    return ids


def test_deleted_own_bundle_is_restored_with_identical_bytes(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    first = _write(store, 3, "one")
    _write(store, 3, "two")
    files = _own_node_files(store)
    assert len(files) == 2
    victim = files[0]
    data = victim.read_bytes()
    store.close()

    victim.unlink()  # what a sync client does when it propagates a delete
    store = _open(root, tmp_path / "a")
    assert victim.read_bytes() == data
    assert store.notices()["restored_own_files"] == 1
    assert all(store.get_node(i) is not None for i in first)
    report = store.doctor()
    assert "restored 1 own files deleted by sync" in report.summary()
    store.close()


def test_restore_happens_on_publisher_tick(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    _write(store, 2, "one")
    _write(store, 2, "two")
    victim = _own_node_files(store)[0]
    data = victim.read_bytes()
    victim.unlink()
    store._restore_last = 0.0
    store.publish_due()
    assert victim.read_bytes() == data
    store.close()


def test_newest_deleted_name_is_not_reused(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    _write(store, 2, "one")
    _write(store, 2, "two")
    newest = _own_node_files(store)[-1]
    newest_bytes = newest.read_bytes()
    newest.unlink()
    # Publish without a restore pass first: the seq must still move past it.
    store._restore_last = float("inf")
    store.make_node(labels=["E"], props={"tag": "three"})
    store._publish_due_unlocked(force=True)
    names = [p.name for p in _own_node_files(store)]
    assert newest.name not in names
    store.close()
    store = _open(root, tmp_path / "a")
    assert newest.read_bytes() == newest_bytes
    assert len(_own_node_files(store)) == 3
    store.close()


def test_compaction_after_delete_loses_nothing_and_resurrects_nothing(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    ids = _write(store, 2, "one") + _write(store, 2, "two") + _write(store, 2, "three")
    before = _own_node_files(store)
    before[1].unlink()
    store.close()
    store = _open(root, tmp_path / "a")  # restore runs before compaction can
    store.compact()
    after = _own_node_files(store)
    assert len(after) == 1
    assert all(store.get_node(i) is not None for i in ids)
    # Superseded files are retired: deleting the new file's predecessors stays.
    store.close()
    store = _open(root, tmp_path / "a")
    assert _own_node_files(store) == after
    assert all(store.get_node(i) is not None for i in ids)
    store.close()


def test_mass_delete_is_held_and_doctor_restore_forces_it(tmp_path: Path, caplog) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    _write(store, 2, "one")
    _write(store, 2, "two")
    files = _own_node_files(store)
    for path in files:
        path.unlink()
    store._restore_last = 0.0
    with caplog.at_level(logging.WARNING):
        store.publish_due()
    assert not any(p.exists() for p in files)
    assert store.problems()["own_restore_held"] == 2
    assert "doctor --restore" in caplog.text
    report = store.doctor(restore=True)
    assert all(p.exists() for p in files)
    assert len(report.restored_own_files) == 2
    assert "own_restore_held" not in store.problems()
    store.close()


def test_own_file_with_other_bytes_is_reported_not_touched(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    _write(store, 2, "one")
    victim = _own_node_files(store)[0]
    store.close()
    original = victim.read_bytes()
    victim.write_bytes(original + original)
    store = _open(root, tmp_path / "a")
    assert victim.read_bytes() == original + original
    assert store.problems()["own_files_changed"] == 1
    assert "own bundle changed" in store.doctor().summary()
    store.close()


def test_other_writers_missing_file_is_not_restored(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    a = _open(root, tmp_path / "a")
    _write(a, 2, "a")
    a.close()
    b = _open(root, tmp_path / "b")
    _write(b, 2, "b")
    a_file = next(p for p in (root / "nodes").rglob("*.jsonl") if a.writer_id in p.name)
    a_file.unlink()
    b.close()
    b = _open(root, tmp_path / "b")
    assert not a_file.exists()  # REQ-078, not this writer's file
    assert "restored_own_files" not in b.notices()
    b.close()


def test_store_from_older_version_adopts_its_files(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    _write(store, 2, "one")
    _write(store, 2, "two")
    store.close()
    ledger = store._ledger.root
    for path in sorted(ledger.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    ledger.rmdir()
    store = _open(root, tmp_path / "a")  # no ledger: like a 0.1.4 store
    store.close()
    victim = _own_node_files(store)[0]
    data = victim.read_bytes()
    victim.unlink()
    store = _open(root, tmp_path / "a")
    assert victim.read_bytes() == data
    store.close()


def test_crash_between_publish_and_ledger_line_is_recorded(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = _open(root, tmp_path / "a")
    _write(store, 2, "one")
    store.make_node(labels=["E"], props={"tag": "two"})
    real = store._ledger.record
    calls = {"n": 0}

    def crash(*args, **kwargs):
        calls["n"] += 1
        raise OSError("simulated crash after publish")

    store._ledger.record = crash  # type: ignore[method-assign]
    try:
        store._publish_due_unlocked(force=True)
    except OSError:
        pass
    store._ledger.record = real  # type: ignore[method-assign]
    assert calls["n"] == 1
    # The active log was not truncated, so the next publish finds the file
    # already sealed and records it rather than publishing a duplicate.
    store._publish_due_unlocked(force=True)
    files = _own_node_files(store)
    assert len(files) == 2
    assert all(store._ledger.get("nodes", p.name) for p in files)
    store.close()


def test_doctor_restore_cli_flag(tmp_path: Path, capsys) -> None:
    root = tmp_path / "g"
    init_graph(root)
    assert cli_main(["doctor", "--restore", str(root)]) == 0
    out = capsys.readouterr().out
    assert "restored 0 own files deleted by sync" in out
    assert "own files missing, restore held" in out
