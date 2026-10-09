"""Conflict copies that rclone bisync names by appending a suffix after .jsonl.

rclone bisync renames both sides of a conflict to ``NN.jsonl.conflict1`` and
``NN.jsonl.conflict2`` (``NN.jsonl..path1`` / ``..path2`` before v1.66) and
removes ``NN.jsonl``. Those files used to be ignored, so every record in the
shard disappeared from both replicas although the bytes were still on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.format import format_timestamp
from graph_ted_db.store.paths import is_record_file_name
from graph_ted_db.store.records import EdgeRecord, NodeRecord, Tombstone

A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"


def _open(tmp_path: Path, name: str = "g") -> GraphStore:
    root = tmp_path / name
    init_graph(root, name="demo", exist_ok=True)
    return GraphStore.open(root, data_dir=tmp_path / f"data-{name}")


def _node(rec_id: str, name: str, at: str | None = None) -> NodeRecord:
    return NodeRecord(
        id=rec_id, updated_at=at or format_timestamp(), labels=("Entity",), props={"name": name}
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("00.jsonl", True),
        ("00-DESKTOP-ABC-conflict-2026-08-25.jsonl", True),
        ("00.jsonl.conflict1", True),
        ("00.jsonl.conflict12", True),
        ("00.jsonl.laptop-conflict3", True),
        ("00.jsonl..path1", True),
        ("00.jsonl..path2", True),
        ("deleted.jsonl.conflict2", True),
        ("00.jsonl.1f5c946a.partial", False),
        ("00.jsonl.tmp", False),
        ("00.jsonl.conflict1.tmp", False),
        (".00.jsonl.conflict1", False),
        ("00.jsonl.bak", False),
    ],
)
def test_record_file_names(name: str, expected: bool) -> None:
    assert is_record_file_name(name) is expected


@pytest.mark.parametrize("suffixes", [(".conflict1", ".conflict2"), ("..path1", "..path2")])
def test_bisync_renamed_shard_stays_visible(tmp_path: Path, suffixes: tuple[str, str]) -> None:
    g = _open(tmp_path)
    g.put_node(_node(A, "alice"))
    shard = g.paths.node_shard(A)
    # bisync: both sides renamed, canonical name gone.
    os.replace(shard, shard.with_name(shard.name + suffixes[0]))
    shard.with_name(shard.name + suffixes[1]).write_text(
        _node(B, "bob").to_jsonl() + "\n", encoding="utf-8"
    )
    assert not shard.exists()

    fresh = GraphStore.open(g.root, data_dir=tmp_path / "data-fresh")
    assert {n.props["name"] for n in fresh.iter_nodes()} == {"alice", "bob"}
    assert fresh.get_node(A) is not None and fresh.get_node(B) is not None
    assert len(fresh.doctor().conflict_copies) == 2
    # A write after the rename recreates the canonical shard; everything stays visible.
    fresh.put_node(_node(A, "alice-2"))
    assert fresh.paths.node_shard(A).is_file()
    assert {n.props["name"] for n in fresh.iter_nodes()} == {"alice-2", "bob"}


def test_bisync_renamed_tombstones_still_apply(tmp_path: Path) -> None:
    g = _open(tmp_path)
    g.put_node(_node(A, "alice", "2026-08-25T12:00:00.000000Z"))
    g.put_node(_node(B, "bob", "2026-08-25T12:00:00.000000Z"))
    tomb = Tombstone(id=A, kind="node", updated_at="2026-08-25T13:00:00.000000Z")
    (g.paths.meta_dir / "deleted.jsonl.conflict1").write_text(
        tomb.to_jsonl() + "\n", encoding="utf-8"
    )
    fresh = GraphStore.open(g.root, data_dir=tmp_path / "data-fresh")
    assert fresh.get_node(A) is None
    assert {n.props["name"] for n in fresh.iter_nodes()} == {"bob"}


def test_suffixed_edge_shard_and_cypher(tmp_path: Path) -> None:
    g = _open(tmp_path)
    g.put_node(_node(A, "alice"))
    g.put_node(_node(B, "bob"))
    e_id = "00000000-0000-4000-8000-0000000000e1"
    g.put_edge(
        EdgeRecord(
            id=e_id, updated_at=format_timestamp(), type="KNOWS", from_id=A, to_id=B, props={}
        )
    )
    shard = g.paths.edge_shard(e_id)
    os.replace(shard, shard.with_name(shard.name + ".conflict2"))
    fresh = GraphStore.open(g.root, data_dir=tmp_path / "data-fresh")
    assert fresh.get_edge(e_id) is not None
    rows = fresh.execute("MATCH (a)-[r:KNOWS]->(b) RETURN b.name AS name")
    assert rows == [{"name": "bob"}]


def test_running_store_notices_a_new_suffixed_copy(tmp_path: Path) -> None:
    g = _open(tmp_path)
    g.put_node(_node(A, "alice"))
    assert len(g.execute("MATCH (n:Entity) RETURN n.name AS name")) == 1
    # The sync client drops a conflict copy next to the shard while the store is open.
    shard = g.paths.node_shard(B)
    shard.with_name(shard.name + ".conflict1").write_text(
        _node(B, "bob").to_jsonl() + "\n", encoding="utf-8"
    )
    names = {r["name"] for r in g.execute("MATCH (n:Entity) RETURN n.name AS name")}
    assert names == {"alice", "bob"}


def test_partial_transfer_file_is_ignored(tmp_path: Path) -> None:
    g = _open(tmp_path)
    g.put_node(_node(A, "alice"))
    shard = g.paths.node_shard(A)
    shard.with_name(shard.name + ".1f5c946a.partial").write_text(
        _node(B, "bob").to_jsonl() + "\n", encoding="utf-8"
    )
    fresh = GraphStore.open(g.root, data_dir=tmp_path / "data-fresh")
    assert {n.props["name"] for n in fresh.iter_nodes()} == {"alice"}
