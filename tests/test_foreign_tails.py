"""Only this device's own files are repaired; other devices' files are never modified."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_ted_db import GraphStore, init_graph
from graph_ted_db.store import SharedStoreError
from graph_ted_db.store.format import format_timestamp
from graph_ted_db.store.records import NodeRecord

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"


def _rec(rid: str, name: str) -> NodeRecord:
    return NodeRecord(
        id=rid, updated_at=format_timestamp(), labels=("Entity",), props={"name": name}
    )


def _node(store: GraphStore, rid: str, name: str):
    return store.put_node(_rec(rid, name))


def _names(store: GraphStore) -> set[str]:
    return {n.props["name"] for n in store.iter_nodes()}


def _sealed(store: GraphStore, record_id: str):
    del record_id
    store.publish()
    return next(
        store.paths.nodes_dir.glob(f"{store.writer_id}.[0-9][0-9][0-9][0-9][0-9][0-9].jsonl")
    )


def test_other_device_never_truncates_unterminated_tail(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    dev_a = GraphStore.open(root, data_dir=tmp_path / "a")
    _node(dev_a, A, "alice")
    shard = _sealed(dev_a, A)
    # A record still arriving from sync: complete JSON, no trailing newline yet.
    line = _rec(B, "bob").to_jsonl()
    with shard.open("ab") as handle:
        handle.write(line[: len(line) // 2].encode())
    before = shard.read_bytes()

    dev_b = GraphStore.open(root, data_dir=tmp_path / "b")
    assert shard.read_bytes() == before
    assert _names(dev_b) == {"alice"}
    assert any("unterminated" in s for s in dev_b.skipped_lines)
    report = dev_b.doctor()
    assert report.foreign_torn == [str(shard)]
    assert report.torn_repaired == []
    assert shard.read_bytes() == before
    assert "not modified" in report.summary()

    # Sync finishes delivering the line: B now sees bob, nothing was lost.
    shard.write_bytes(before + line[len(line) // 2 :].encode() + b"\n")
    assert _names(GraphStore.open(root, data_dir=tmp_path / "b")) == {"alice", "bob"}


def test_append_onto_foreign_tail_keeps_its_bytes(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    dev_a = GraphStore.open(root, data_dir=tmp_path / "a")
    _node(dev_a, A, "alice")
    shard = _sealed(dev_a, A)
    with shard.open("ab") as handle:
        handle.write(b'{"id":"frag')
    before = shard.read_bytes()

    dev_b = GraphStore.open(root, data_dir=tmp_path / "b")
    _node(dev_b, A, "alice2")
    # B never appends to A's sealed segment. Its own bytes are a different file.
    assert shard.read_bytes() == before
    own = _sealed(dev_b, A)
    assert own != shard and dev_b.writer_id in own.name
    assert _names(GraphStore.open(root, data_dir=tmp_path / "c")) == {"alice2"}


def test_own_torn_tail_is_repaired(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    dev_a = GraphStore.open(root, data_dir=tmp_path / "a")
    _node(dev_a, A, "alice")
    # The mutable copy is the local active log. A torn tail there is repaired.
    active = dev_a._active_log(dev_a.paths.nodes_dir)
    with active.open("ab") as handle:
        handle.write(b'{"id":"torn')
    report = GraphStore.open(root, data_dir=tmp_path / "a").doctor()
    assert b'{"id":"torn' not in active.read_bytes()
    assert report.foreign_torn == []
    assert _names(GraphStore.open(root, data_dir=tmp_path / "a")) == {"alice"}

    # A sealed segment is never rewritten, even by the writer who published it.
    sealed = _sealed(dev_a, A)
    torn = sealed.read_bytes() + b'{"id":"sealed-torn'
    sealed.write_bytes(torn)
    again = GraphStore.open(root, data_dir=tmp_path / "a")
    assert sealed.read_bytes() == torn
    assert str(sealed) in again.doctor().sealed_torn


def test_compact_rewrites_only_this_writers_segments(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    dev_a = GraphStore.open(root, data_dir=tmp_path / "a")
    _node(dev_a, A, "alice")
    dev_a.compact()
    dev_b = GraphStore.open(root, data_dir=tmp_path / "b")
    dev_b.compact()  # nothing of B's; other writers' files are not a reason to refuse
    _node(dev_b, B, "bob")
    bob = _sealed(dev_b, B)
    bob_bytes = bob.read_bytes()
    GraphStore.open(root, data_dir=tmp_path / "a").compact()
    assert bob.read_bytes() == bob_bytes
    conflict = bob.with_name(bob.name + ".conflict1")
    conflict.write_bytes(bob_bytes)
    with pytest.raises(SharedStoreError):
        GraphStore.open(root, data_dir=tmp_path / "a").compact()
    assert bob.read_bytes() == bob_bytes
