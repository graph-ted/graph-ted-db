"""Format v2: per-writer files, hybrid logical clock, writer ids, format marker."""

from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import socket
from pathlib import Path

import pytest

import graph_ted_db.store.graph as graph_mod
from graph_ted_db import GraphStore, init_graph
from graph_ted_db.store.format import is_writer_id
from graph_ted_db.store.init import load_graph_meta
from graph_ted_db.store.paths import writer_of
from graph_ted_db.store.records import NodeRecord

X = "0f000000-0000-4000-8000-000000000001"


def sync(*roots: Path) -> None:
    """A crude sync client: per file, the newest version overwrites the others.

    No conflict copies at all: the worst case for a shared-file layout.
    """
    names: set[Path] = set()
    for root in roots:
        names |= {p.relative_to(root) for p in root.rglob("*") if p.is_file()}
    for rel in names:
        present = [r / rel for r in roots if (r / rel).is_file()]
        newest = max(present, key=lambda p: (p.stat().st_mtime_ns, p.stat().st_size))
        for root in roots:
            dst = root / rel
            if dst != newest:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(newest, dst)


def _two_devices(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "a" / "g"
    init_graph(a)
    b = tmp_path / "b" / "g"
    shutil.copytree(a, b)
    return a, b


def _open(root: Path, tmp_path: Path, dev: str) -> GraphStore:
    return GraphStore.open(root, data_dir=tmp_path / f"data-{dev}")


def test_concurrent_adds_lose_nothing_even_with_overwriting_sync(tmp_path: Path) -> None:
    a, b = _two_devices(tmp_path)
    sa, sb = _open(a, tmp_path, "a"), _open(b, tmp_path, "b")
    ids_a = {sa.make_node(labels=["E"], props={"by": "a", "i": i}).id for i in range(300)}
    ids_b = {sb.make_node(labels=["E"], props={"by": "b", "i": i}).id for i in range(300)}
    sync(a, b)
    for root, dev in ((a, "a"), (b, "b")):
        got = {n.id for n in _open(root, tmp_path, dev).iter_nodes()}
        assert got == ids_a | ids_b
    # No file was written by both devices.
    writers = {writer_of(p.name) for p in (a / "nodes").iterdir()}
    assert writers == {sa.writer_id, sb.writer_id}


def test_concurrent_edit_and_delete_converge_to_same_winner(tmp_path: Path) -> None:
    a, b = _two_devices(tmp_path)
    sa = _open(a, tmp_path, "a")
    sa.make_node(record_id=X, labels=["E"], props={"v": 0})
    sync(a, b)
    sb = _open(b, tmp_path, "b")
    assert sb.get_node(X) is not None
    sa.make_node(record_id=X, labels=["E"], props={"v": "edit-a"})
    sb.delete_node(X)
    sync(a, b)
    ra, rb = _open(a, tmp_path, "a"), _open(b, tmp_path, "b")
    assert ra.get_node(X) == rb.get_node(X)


def test_same_timestamp_tiebreak_is_deterministic(tmp_path: Path) -> None:
    a, b = _two_devices(tmp_path)
    sa, sb = _open(a, tmp_path, "a"), _open(b, tmp_path, "b")
    ts = "2026-10-09T12:00:00.000000Z"
    sa.put_node(NodeRecord(id=X, updated_at=ts, labels=("E",), props={"by": "a"}))
    sb.put_node(NodeRecord(id=X, updated_at=ts, labels=("E",), props={"by": "b"}))
    sync(a, b)
    expect = "a" if sa.writer_id > sb.writer_id else "b"
    for root, dev in ((a, "a"), (b, "b")):
        assert _open(root, tmp_path, dev).get_node(X).props["by"] == expect


def test_edit_after_seeing_wins_despite_slow_clock(tmp_path: Path, monkeypatch) -> None:
    a, b = _two_devices(tmp_path)
    sa = _open(a, tmp_path, "a")
    sa.make_node(record_id=X, labels=["E"], props={"v": "a"})
    sync(a, b)
    # Device B's clock is a day behind.
    monkeypatch.setattr(graph_mod, "format_timestamp", lambda: "2026-01-01T00:00:00.000000Z")
    sb = _open(b, tmp_path, "b")
    assert sb.get_node(X).props["v"] == "a"
    edited = sb.make_node(record_id=X, labels=["E"], props={"v": "b"})
    assert edited.updated_at >= sa.get_node(X).updated_at
    monkeypatch.undo()
    sync(a, b)
    assert _open(a, tmp_path, "a").get_node(X).props["v"] == "b"


def test_counter_orders_writes_in_the_same_microsecond(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "g"
    init_graph(root)
    monkeypatch.setattr(graph_mod, "format_timestamp", lambda: "2026-10-09T12:00:00.000000Z")
    g = GraphStore.open(root, data_dir=tmp_path / "d")
    for i in range(5):
        g.make_node(record_id=X, labels=["E"], props={"name": f"z{4 - i}"})
    assert g.get_node(X).props["name"] == "z0"
    assert g.get_node(X).counter == 4


def test_writer_id_is_random_local_and_replaced_if_lost(tmp_path: Path) -> None:
    root = tmp_path / "g"
    meta = init_graph(root)
    data = tmp_path / "d"
    w1 = GraphStore.open(root, data_dir=data).writer_id
    assert is_writer_id(w1)
    assert GraphStore.open(root, data_dir=data).writer_id == w1
    assert (data / meta.id / "writer.json").is_file()
    assert not list(root.rglob("writer.json"))  # never in the synced folder
    os.remove(data / meta.id / "writer.json")
    w2 = GraphStore.open(root, data_dir=data).writer_id
    assert is_writer_id(w2) and w2 != w1
    assert GraphStore.open(root, data_dir=tmp_path / "other").writer_id not in {w1, w2}


def test_writer_id_contains_no_user_os_or_host_names(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    g = GraphStore.open(root, data_dir=tmp_path / "d")
    g.make_node(labels=["E"], props={})
    names = set()
    for fn in (getpass.getuser, socket.gethostname, platform.node, platform.system):
        try:
            value = fn()
        except Exception:  # noqa: BLE001 - some CI images have no login name
            continue
        names |= {v.lower() for v in value.replace(".", " ").split() if len(v) >= 3}
    texts = [g.writer_id]
    texts += [p.name for p in root.rglob("*")]
    texts += [p.read_text("utf-8") for p in (root / "meta" / "writers").iterdir()]
    for text in texts:
        for name in names:
            assert name not in text.lower(), (name, text)


def test_format_marker_and_writers_list(tmp_path: Path) -> None:
    a, b = _two_devices(tmp_path)
    sa, sb = _open(a, tmp_path, "a"), _open(b, tmp_path, "b")
    sync(a, b)
    meta = load_graph_meta(a)
    assert meta.format_version == 2 and meta.extras["layout"] == "per-writer"
    assert _open(a, tmp_path, "a").writers() == sorted([sa.writer_id, sb.writer_id])
    reg = json.loads((a / "meta" / "writers" / f"{sa.writer_id}.json").read_text())
    assert set(reg) == {"writer", "format_version", "created_at"}


def test_v1_store_is_read_and_upgraded(tmp_path: Path) -> None:
    root = tmp_path / "g"
    meta = init_graph(root)
    raw = meta.to_dict()
    raw["format_version"] = 1
    raw.pop("layout", None)
    (root / "graph.json").write_text(json.dumps(raw))
    old = NodeRecord(id=X, updated_at="2026-08-25T12:00:00.000000Z", labels=("E",), props={})
    (root / "nodes" / "0f.jsonl").write_text(old.to_jsonl() + "\n")
    g = GraphStore.open(root, data_dir=tmp_path / "d")
    assert g.get_node(X) is not None
    assert load_graph_meta(root).format_version == 2
    g.make_node(record_id=X, labels=["E"], props={"new": True})
    assert (root / "nodes" / "0f.jsonl").read_text() == old.to_jsonl() + "\n"
    assert g.get_node(X).props == {"new": True}
    assert g.doctor().legacy_shared_files == [str(root / "nodes" / "0f.jsonl")]


def test_doctor_on_half_synced_copy_reports_and_modifies_nothing(tmp_path: Path) -> None:
    a, b = _two_devices(tmp_path)
    sa = _open(a, tmp_path, "a")
    for i in range(20):
        sa.make_node(labels=["E"], props={"i": i})
    # Half-synced: B gets A's node files, one cut mid-line, but no registration yet.
    for p in (a / "nodes").iterdir():
        dst = b / "nodes" / p.name
        dst.write_bytes(p.read_bytes())
    victim = sorted((b / "nodes").iterdir())[0]
    victim.write_bytes(victim.read_bytes()[:-5])
    before = {p: p.read_bytes() for p in (b / "nodes").iterdir()}
    sb = _open(b, tmp_path, "b")
    report = sb.doctor()
    assert {p: p.read_bytes() for p in before} == before
    assert report.foreign_torn == [str(victim)]
    assert report.unregistered_writers == [sa.writer_id]
    assert report.dangling_edges_tombstoned == []
    assert report.torn_repaired == []


def test_compact_on_single_writer_keeps_only_own_files(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    g = GraphStore.open(root, data_dir=tmp_path / "d")
    for i in range(3):
        g.make_node(record_id=X, labels=["E"], props={"i": i})
    g.compact()
    lines = g.paths.node_shard(X).read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["props"] == {"i": 2}


@pytest.mark.parametrize("bad", ["", "w123", "W" + "a" * 16, "w" + "g" * 16, "laptop-name"])
def test_writer_id_validation(bad: str) -> None:
    assert not is_writer_id(bad)
