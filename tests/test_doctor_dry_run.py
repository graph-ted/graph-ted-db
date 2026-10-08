"""doctor reports dangling edges by default and only tombstones them with fix.

A synced folder can hold edges whose nodes have not arrived yet. Tombstoning
them then is permanent and syncs to every device, so the default is a report.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from graph_ted_db.cli import main
from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.format import format_timestamp
from graph_ted_db.store.records import EdgeRecord, NodeRecord

N = 20


def _node_id(i: int) -> str:
    return f"{i:02x}000000-0000-4000-8000-{i:012x}"


def _edge_id(i: int) -> str:
    return f"{i:02x}e00000-0000-4000-8000-{i:012x}"


def _writer_store(tmp_path: Path) -> Path:
    root = tmp_path / "writer"
    init_graph(root, name="shared")
    g = GraphStore.open(root, data_dir=tmp_path / "writer-data")
    for i in range(N):
        g.put_node(NodeRecord(id=_node_id(i), updated_at=format_timestamp(), labels=("Entity",), props={"i": i}))
    for i in range(N):
        g.put_edge(
            EdgeRecord(id=_edge_id(i), updated_at=format_timestamp(), type="RELATES_TO",
                       from_id=_node_id(i), to_id=_node_id((i + 1) % N), props={})
        )
    return root


def _half_synced_copy(src: Path, dst: Path) -> Path:
    """Edges and meta arrived; nodes have not."""
    dst.mkdir(parents=True)
    shutil.copy2(src / "graph.json", dst / "graph.json")
    for sub in ("edges", "meta"):
        shutil.copytree(src / sub, dst / sub)
    return dst


def _finish_sync(src: Path, dst: Path) -> None:
    shutil.copytree(src / "nodes", dst / "nodes", dirs_exist_ok=True)


def test_default_doctor_on_half_synced_copy_deletes_nothing(tmp_path: Path) -> None:
    src = _writer_store(tmp_path)
    copy = _half_synced_copy(src, tmp_path / "reader")
    deleted_before = (copy / "meta" / "deleted.jsonl").read_bytes() if (copy / "meta" / "deleted.jsonl").exists() else b""

    g = GraphStore.open(copy, data_dir=tmp_path / "reader-data")
    report = g.doctor()
    assert len(report.dangling_edges_found) == N
    assert report.dangling_edges_tombstoned == []
    assert report.fix is False
    deleted_after = (copy / "meta" / "deleted.jsonl").read_bytes() if (copy / "meta" / "deleted.jsonl").exists() else b""
    assert deleted_after == deleted_before

    _finish_sync(src, copy)
    fresh = GraphStore.open(copy, data_dir=tmp_path / "reader-data-2")
    assert len(list(fresh.iter_edges())) == N
    assert fresh.doctor().dangling_edges_found == []


def test_doctor_fix_on_half_synced_copy_deletes_permanently(tmp_path: Path) -> None:
    src = _writer_store(tmp_path)
    copy = _half_synced_copy(src, tmp_path / "reader")
    g = GraphStore.open(copy, data_dir=tmp_path / "reader-data")
    report = g.doctor(fix=True)
    assert len(report.dangling_edges_tombstoned) == N
    assert report.dangling_edges_found == report.dangling_edges_tombstoned

    # The nodes arrive later, but the tombstones are newer than the edges.
    _finish_sync(src, copy)
    fresh = GraphStore.open(copy, data_dir=tmp_path / "reader-data-2")
    assert len(list(fresh.iter_nodes())) == N
    assert list(fresh.iter_edges()) == []


def test_cli_doctor_reports_and_fix_deletes(tmp_path: Path, capsys) -> None:
    src = _writer_store(tmp_path)
    copy = _half_synced_copy(src, tmp_path / "reader")

    assert main(["doctor", str(copy)]) == 0
    out = capsys.readouterr().out
    assert f"dangling edges found: {N}" in out
    assert "dangling edges tombstoned: 0" in out
    assert "would tombstone" in out and "--fix" in out

    assert main(["doctor", "--fix", str(copy)]) == 0
    out = capsys.readouterr().out
    assert f"dangling edges tombstoned: {N}" in out

    _finish_sync(src, copy)
    assert list(GraphStore.open(copy, data_dir=tmp_path / "reader-data-3").iter_edges()) == []
