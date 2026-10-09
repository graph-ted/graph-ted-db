"""Data-loss risks: multi-process writers, placeholders, write errors, offline
tombstones, and rebuilding local state. See docs/format.md."""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import graph_ted_db.store.graph as graph_mod
import graph_ted_db.store.jsonl as jsonl_mod
from graph_ted_db import GraphStore, init_graph
from graph_ted_db.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
X = "0f000000-0000-4000-8000-000000000001"

_WRITER = """
import sys
from pathlib import Path
from graph_ted_db import GraphStore
root, data, tag, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
g = GraphStore.open(root, data_dir=Path(data))
for i in range(n):
    g.make_node(labels=["E"], props={"by": tag, "i": i})
    if i % 10 == 0:
        g.execute("CREATE (n:Batch {by: $by, i: $i})", {"by": tag, "i": i})
"""


# --- 1. several processes on one device ---------------------------------


def test_multiple_processes_on_one_device_lose_nothing(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    data = tmp_path / "appdata"  # shared: same device, same writer id, same LOCK
    n, procs = 60, 4
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    running = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, str(root), str(data), f"p{k}", str(n)], env=env
        )
        for k in range(procs)
    ]
    assert [p.wait(timeout=180) for p in running] == [0] * procs
    g = GraphStore.open(root, data_dir=data)
    nodes = list(g.iter_nodes())
    entity = {(x.props["by"], x.props["i"]) for x in nodes if "E" in x.labels}
    assert entity == {(f"p{k}", i) for k in range(procs) for i in range(n)}
    assert sum(1 for x in nodes if "Batch" in x.labels) == procs * (n // 10)
    assert g.problems() == dict.fromkeys(g.problems(), 0)
    assert len(g.writers()) == 1
    for path in root.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # no fused or torn lines


# --- 4. cloud-only placeholders and empty files ------------------------


def test_cloud_only_and_empty_files_are_reported(tmp_path: Path, monkeypatch, caplog) -> None:
    root = tmp_path / "g"
    init_graph(root)
    g = GraphStore.open(root, data_dir=tmp_path / "a")
    g.make_node(record_id=X, labels=["E"], props={})
    placeholder = g.paths.node_shard(X)
    empty = root / "nodes" / "aa.w0000000000000001.jsonl"
    empty.write_bytes(b"")
    monkeypatch.setattr(
        graph_mod, "is_cloud_only", lambda st: st.st_size == placeholder.stat().st_size
    )
    with caplog.at_level(logging.WARNING, logger="graph_ted_db"):
        other = GraphStore.open(root, data_dir=tmp_path / "b")
    assert other.problems()["cloud_only_files"] == 1
    assert other.problems()["empty_record_files"] == 1
    assert "doctor" in caplog.text
    report = other.doctor()
    assert report.cloud_only == [str(placeholder)]
    assert report.empty_files == [str(empty)]
    assert "always keep on this device" in report.summary()


def test_is_cloud_only_reads_windows_attributes() -> None:
    class St:
        st_file_attributes = 0x400000  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

    class Local:
        st_file_attributes = 0x20  # FILE_ATTRIBUTE_ARCHIVE

    assert graph_mod.is_cloud_only(St())  # type: ignore[arg-type]
    assert not graph_mod.is_cloud_only(Local())  # type: ignore[arg-type]
    assert not graph_mod.is_cloud_only(os.stat(__file__))


# --- 5. disk full / permission errors and bad lines --------------------


def test_skipped_lines_warn_on_open_and_show_in_info_and_health(
    tmp_path: Path, caplog, capsys
) -> None:
    root = tmp_path / "g"
    init_graph(root)
    g = GraphStore.open(root, data_dir=tmp_path / "a")
    g.make_node(record_id=X, labels=["E"], props={})
    with g.paths.node_shard(X).open("a", encoding="utf-8") as fh:
        fh.write('{"not": "a record"}\n')
    with caplog.at_level(logging.WARNING, logger="graph_ted_db"):
        reopened = GraphStore.open(root, data_dir=tmp_path / "a")
    assert reopened.problems()["skipped_lines"] == 1
    assert "skipped_lines" in caplog.text
    os.environ["GRAPH_TED_DB_DATA"] = str(tmp_path / "a")
    try:
        assert cli_main(["info", str(root), "--check"]) == 1
    finally:
        del os.environ["GRAPH_TED_DB_DATA"]
    assert "skipped_lines=1" in capsys.readouterr().out
    from graph_ted_db.server.http import _health_payload

    assert _health_payload(reopened)["problems"]["skipped_lines"] == 1


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES])
def test_write_errors_fail_loudly_and_leave_no_bad_lines(tmp_path: Path, monkeypatch, code) -> None:
    root = tmp_path / "g"
    init_graph(root)
    data = tmp_path / "a"
    g = GraphStore.open(root, data_dir=data)
    g.make_node(record_id=X, labels=["E"], props={"v": "kept"})
    real_write = jsonl_mod._AppendBatch.append
    real_fsync = os.fsync

    def failing_fsync(fd):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError) as info:
        g.make_node(labels=["E"], props={"v": "lost"})
    assert info.value.errno == code
    with pytest.raises(OSError):
        g.execute_many([("CREATE (n:T {i: $i})", {"i": i}) for i in range(5)])
    monkeypatch.setattr(os, "fsync", real_fsync)
    monkeypatch.setattr(jsonl_mod._AppendBatch, "append", real_write)
    reopened = GraphStore.open(root, data_dir=data)
    assert reopened.get_node(X).props == {"v": "kept"}
    # Each failed write is all-or-nothing after reopen, never a fragment.
    t_nodes = [n for n in reopened.iter_nodes() if "T" in n.labels]
    assert len(t_nodes) in (0, 5)
    assert reopened.problems() == dict.fromkeys(reopened.problems(), 0)


def test_partial_write_is_repaired_in_own_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "g"
    init_graph(root)
    data = tmp_path / "a"
    g = GraphStore.open(root, data_dir=data)
    g.make_node(record_id=X, labels=["E"], props={"v": "kept"})
    shard = g.paths.node_shard(X)
    # Disk filled up half-way through a line.
    with shard.open("ab") as fh:
        fh.write(b'{"id":"0f000000-0000-4000-8000-0000000000')
    reopened = GraphStore.open(root, data_dir=data)
    assert shard.read_bytes().endswith(b"\n")
    assert reopened.get_node(X).props == {"v": "kept"}
    reopened.make_node(record_id=X, labels=["E"], props={"v": "next"})
    assert reopened.problems()["skipped_lines"] == 0


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root")
def test_read_only_folder_raises_permission_error(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    g = GraphStore.open(root, data_dir=tmp_path / "a")
    g.make_node(labels=["E"], props={})
    os.chmod(root / "nodes", 0o500)
    try:
        with pytest.raises(PermissionError):
            g.make_node(record_id="ff000000-0000-4000-8000-000000000001", labels=["E"], props={})
    finally:
        os.chmod(root / "nodes", 0o700)


# --- 6. tombstones and an old offline device ---------------------------


def test_old_offline_device_cannot_bring_deleted_data_back(tmp_path: Path) -> None:
    from tests.test_per_writer import sync

    a = tmp_path / "a" / "g"
    init_graph(a)
    b = tmp_path / "b" / "g"
    shutil.copytree(a, b)
    sa = GraphStore.open(a, data_dir=tmp_path / "da")
    ids = [sa.make_node(labels=["E"], props={"i": i}).id for i in range(20)]
    sync(a, b)
    sb = GraphStore.open(b, data_dir=tmp_path / "db")
    sb.make_node(
        record_id=ids[0], labels=["E"], props={"i": "b-old-edit"}
    )  # B edits, then goes offline
    stale_b = {p: p.read_bytes() for p in b.rglob("*.jsonl")}
    for rid in ids:
        sa.delete_node(rid)
    sync(a, b)  # B is offline: pretend it never received this
    for p, data in stale_b.items():
        p.write_bytes(data)
    for p in list(b.rglob("deleted.*.jsonl")):
        if p not in stale_b:
            p.unlink()
    # Months later B comes back with its old files and syncs.
    sync(a, b)
    for root, dd in ((a, "da"), (b, "db")):
        assert list(GraphStore.open(root, data_dir=tmp_path / dd).iter_nodes()) == []
    # Tombstones are kept forever in 0.1.0 (no cleanup yet).
    assert sum(1 for p in a.rglob("deleted.*.jsonl") for _ in p.open()) >= 20


# --- 7. local state is rebuildable --------------------------------------


def test_wiping_app_data_rebuilds_identical_results(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    data = tmp_path / "a"
    g = GraphStore.open(root, data_dir=data)
    a = g.make_node(labels=["Person"], props={"name": "a"})
    b = g.make_node(labels=["Person"], props={"name": "b"})
    g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id)
    q = "MATCH (x:Person)-[:KNOWS]->(y) RETURN x.name AS x, y.name AS y"
    before = g.execute(q)
    old_writer = g.writer_id
    shutil.rmtree(data)  # index, WAL, LOCK, writer id
    (root / "meta" / "labels.json").write_text("{not json")
    fresh = GraphStore.open(root, data_dir=data)
    assert fresh.execute(q) == before
    assert fresh.writer_id != old_writer
    fresh.doctor()
    assert json.loads((root / "meta" / "labels.json").read_text())["node_labels"] == ["Person"]
