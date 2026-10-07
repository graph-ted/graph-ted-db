"""Process crashes must lose at most the in-flight transaction."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.records import NodeRecord
from tests.crash_worker import (
    DROP_EDGE_SEQ,
    DROP_SEQ,
    KEEP_EDGE_SEQ,
    KEEP_SEQS,
    apply_one,
    apply_txn,
    record_id,
)

WORKER = Path(__file__).with_name("crash_worker.py")
RANDOM_AFTER_ACK = 12
RANDOM_IMMEDIATE = 8


def _outcome(store: GraphStore, txn: int) -> str:
    nodes = [node for node in store.iter_nodes() if node.props.get("txn") == txn]
    edges = [edge for edge in store.iter_edges() if edge.props.get("txn") == txn]
    if not nodes and not edges:
        return "absent"
    keep_ids = {record_id(txn, seq) for seq in KEEP_SEQS}
    node_ids = {node.id for node in nodes}
    edge_ids = {edge.id for edge in edges}
    if node_ids == keep_ids and edge_ids == {record_id(txn, KEEP_EDGE_SEQ)}:
        if record_id(txn, DROP_SEQ) in node_ids:
            return "partial"
        if record_id(txn, DROP_EDGE_SEQ) in edge_ids:
            return "partial"
        return "present"
    return "partial"


def _assert_store(root: Path, data: Path, acked: set[int], intent: int | None) -> None:
    store = GraphStore.open(root, data_dir=data)
    again = GraphStore.open(root, data_dir=data)
    for opened in (store, again):
        seen: set[int] = set()
        for node in opened.iter_nodes():
            txn = node.props.get("txn")
            if isinstance(txn, int):
                seen.add(txn)
        for edge in opened.iter_edges():
            txn = edge.props.get("txn")
            if isinstance(txn, int):
                seen.add(txn)
        allowed = set(acked)
        if intent is not None and intent not in acked:
            allowed.add(intent)
        extra = seen - allowed
        assert extra == set(), extra
        for txn in acked:
            assert _outcome(opened, txn) == "present", (txn, _outcome(opened, txn))
        if intent is not None and intent not in acked:
            state = _outcome(opened, intent)
            assert state in ("present", "absent"), state
    wal = data / store.meta.id / "wal"
    if wal.is_dir():
        # An empty reused commit file is not a transaction.
        leftover = [path for path in wal.glob("*.jsonl") if path.stat().st_size > 0]
        assert leftover == []


def _run(
    tmp: Path,
    *,
    crash_at: str | None = None,
    delay: float | None = None,
    after_ack: bool = False,
    single: bool = False,
) -> tuple[set[int], int | None, int, str]:
    root = tmp / "g"
    data = tmp / "data"
    data.mkdir()
    init_graph(root, name="crash")
    env = os.environ.copy()
    env.pop("GRAPH_TED_DB_CRASH_AT", None)
    if crash_at:
        env["GRAPH_TED_DB_CRASH_AT"] = crash_at
    cmd = [sys.executable, str(WORKER), str(root), str(data)]
    if single:
        cmd.append("single")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None
    first = b""
    try:
        if crash_at:
            proc.wait(timeout=20)
        elif after_ack:
            first = proc.stdout.readline()
            time.sleep(delay or 0)
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)
        else:
            time.sleep(delay or 0)
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    out = (first + proc.stdout.read()).decode("utf-8", errors="replace")
    err = proc.stderr.read().decode("utf-8", errors="replace")
    acked = {int(line) for line in out.splitlines() if line.strip().isdigit()}
    intent_path = data / "intent"
    intent = int(intent_path.read_text()) if intent_path.is_file() else None
    return acked, intent, proc.returncode or 0, err


def test_transaction_fsyncs_each_file_once(tmp_path: Path, monkeypatch):
    """11 records share files. Labels and deleted.jsonl are not fsynced per record."""
    import os

    calls = {"n": 0}
    real = os.fsync

    def counting(fd):
        calls["n"] += 1
        return real(fd)

    monkeypatch.setattr(os, "fsync", counting)
    root = tmp_path / "g"
    data = tmp_path / "data"
    init_graph(root, name="fsyncs")
    store = GraphStore.open(root, data_dir=data)
    calls["n"] = 0
    apply_txn(store, 0)
    # 7 nodes, 2 edges, 2 tombstones. Per-record fsync plus per-record labels
    # would be well above one fsync per record.
    assert calls["n"] < 22
    assert _outcome(store, 0) == "present"


def test_single_statement_has_no_commit_record(tmp_path: Path):
    root = tmp_path / "g"
    data = tmp_path / "data"
    init_graph(root, name="one")
    store = GraphStore.open(root, data_dir=data)
    apply_one(store, 0)
    assert _ids(store) == {record_id(0, 0)}
    wal = data / store.meta.id / "wal"
    assert not wal.is_dir() or list(wal.glob("*.jsonl")) == []


def test_single_line_dies_during_fsync(tmp_path: Path):
    acked, intent, code, err = _run(tmp_path, crash_at="data-fsync", single=True)
    assert code < 0, err
    assert acked == set(), err
    assert intent == 0
    store = GraphStore.open(tmp_path / "g", data_dir=tmp_path / "data")
    hits = [node for node in store.iter_nodes() if node.props.get("txn") == 0]
    assert len(hits) in (0, 1)
    assert _ids(store) <= {record_id(0, 0)}


def test_single_line_autocommit_sigkill(tmp_path: Path):
    """A one-record statement stays present or absent across a real SIGKILL."""
    for index in range(6):
        trial = tmp_path / f"s{index}"
        trial.mkdir()
        if index < 3:
            acked, intent, code, err = _run(trial, delay=index * 0.001, single=True)
        else:
            acked, intent, code, err = _run(
                trial, after_ack=True, delay=(index % 3) * 0.001, single=True
            )
        assert code < 0, err
        root = trial / "g"
        data = trial / "data"
        store = GraphStore.open(root, data_dir=data)
        seen = {
            node.props.get("txn")
            for node in store.iter_nodes()
            if isinstance(node.props.get("txn"), int)
        }
        allowed = set(acked)
        if intent is not None and intent not in acked:
            allowed.add(intent)
        assert seen <= allowed
        for txn in acked:
            assert record_id(txn, 0) in _ids(store)
        if intent is not None and intent not in acked:
            hits = [node for node in store.iter_nodes() if node.props.get("txn") == intent]
            assert len(hits) in (0, 1)


def test_transaction_outcome_shape(tmp_path: Path):
    root = tmp_path / "g"
    data = tmp_path / "data"
    init_graph(root, name="shape")
    store = GraphStore.open(root, data_dir=data)
    apply_txn(store, 3)
    assert _outcome(store, 3) == "present"
    assert _outcome(store, 4) == "absent"


@pytest.mark.parametrize(
    "crash_at",
    ["before-wal", "wal-fsync", "after-wal", "mid-apply", "data-fsync", "before-unlink"],
)
def test_forced_crash_point(tmp_path: Path, crash_at: str):
    acked, intent, code, err = _run(tmp_path, crash_at=crash_at)
    assert code < 0, err
    assert acked == set(), (acked, err)
    assert intent == 0
    root = tmp_path / "g"
    data = tmp_path / "data"
    store = GraphStore.open(root, data_dir=data)
    state = _outcome(store, 0)
    if crash_at in ("before-wal", "wal-fsync"):
        assert state == "absent", state
    else:
        assert state == "present", state
    _assert_store(root, data, acked, intent)


def test_random_sigkill_many_iterations(tmp_path: Path):
    for index in range(RANDOM_IMMEDIATE + RANDOM_AFTER_ACK):
        trial = tmp_path / f"t{index}"
        trial.mkdir()
        if index < RANDOM_IMMEDIATE:
            acked, intent, code, err = _run(trial, delay=index * 0.002)
        else:
            acked, intent, code, err = _run(
                trial,
                after_ack=True,
                delay=((index - RANDOM_IMMEDIATE) % 5) * 0.001,
            )
        assert code < 0, err
        _assert_store(trial / "g", trial / "data", acked, intent)


def _ids(store: GraphStore) -> set[str]:
    return {node.id for node in store.iter_nodes()}


def test_torn_suffix_is_not_data(tmp_path: Path):
    root = tmp_path / "g"
    data = tmp_path / "data"
    init_graph(root, name="torn")
    store = GraphStore.open(root, data_dir=data)
    apply_txn(store, 1)
    assert _outcome(store, 1) == "present"
    node_shard = store.paths.node_shard(record_id(1, 0))
    edge_shard = store.paths.edge_shard(record_id(1, KEEP_EDGE_SEQ))
    deleted = store.paths.deleted_jsonl
    assert node_shard.is_file()
    assert edge_shard.is_file()
    assert deleted.is_file()
    phantom = NodeRecord(
        id=record_id(99, 0),
        updated_at="2026-08-25T12:00:00.000000Z",
        labels=("Entity",),
        props={"name": "phantom", "txn": 99},
    )
    for path in (node_shard, edge_shard, deleted):
        with path.open("ab") as handle:
            handle.write(phantom.to_jsonl().encode("utf-8"))
    reopened = GraphStore.open(root, data_dir=data)
    assert _outcome(reopened, 1) == "present"
    assert record_id(99, 0) not in _ids(reopened)
    assert _outcome(reopened, 99) == "absent"
    assert all(not line or line.endswith("}") for line in deleted.read_text().splitlines())


def test_truncated_final_line_is_skipped(tmp_path: Path):
    root = tmp_path / "g"
    data = tmp_path / "data"
    init_graph(root, name="chop")
    store = GraphStore.open(root, data_dir=data)
    apply_txn(store, 2)
    apply_txn(store, 3)
    keep = record_id(2, 1)
    chopped_id = record_id(3, 4)
    node_shard = store.paths.node_shard(chopped_id)
    original = node_shard.read_bytes()
    assert original.endswith(b"\n")
    assert original.count(b"\n") >= 1
    node_shard.write_bytes(original[:-12])
    deleted = store.paths.deleted_jsonl
    deleted_bytes = deleted.read_bytes()
    assert deleted_bytes.endswith(b"\n")
    deleted.write_bytes(deleted_bytes[:-8])
    edge_shard = store.paths.edge_shard(record_id(3, KEEP_EDGE_SEQ))
    edge_bytes = edge_shard.read_bytes()
    edge_shard.write_bytes(edge_bytes[:-6])

    reopened = GraphStore.open(root, data_dir=data)
    assert keep in _ids(reopened)
    assert chopped_id not in _ids(reopened)
    assert record_id(3, KEEP_EDGE_SEQ) not in {edge.id for edge in reopened.iter_edges()}
    # The torn tail is not a record, and a different transaction is intact.
    assert _outcome(reopened, 2) == "present"
    text = node_shard.read_text(encoding="utf-8")
    assert "phantom" not in text
    assert not text or text.endswith("\n")
    assert deleted.read_bytes().endswith(b"\n") or deleted.read_bytes() == b""
