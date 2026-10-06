"""NetworkX-like aliases are thin wrappers; primary get/iter sees the same records."""

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.aliases import DEFAULT_REL_TYPE, _ALIAS_NAMESPACE, alias_record_id


def _open(tmp_path: Path, name: str = "g") -> GraphStore:
    root = tmp_path / name
    init_graph(root, name="demo")
    return GraphStore.open(root, data_dir=tmp_path / "locks")


def test_alias_record_id_rules():
    assert _ALIAS_NAMESPACE == uuid5(
        NAMESPACE_URL, "https://github.com/graph-ted/graph-ted-db/nx-alias"
    )
    assert alias_record_id("alice") == alias_record_id("alice")
    assert alias_record_id("alice") != alias_record_id("bob")
    assert alias_record_id(1) != alias_record_id("1")
    assert alias_record_id(1) == alias_record_id(1)
    uid = "550e8400-e29b-41d4-a716-446655440000"
    assert alias_record_id(uid) == uid
    assert alias_record_id(uid.upper()) == uid
    assert GraphStore.alias_record_id("alice") == alias_record_id("alice")
    with pytest.raises(TypeError):
        alias_record_id(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        alias_record_id(1.5)  # type: ignore[arg-type]


def test_add_node_visible_via_primary_get_and_iter(tmp_path: Path):
    g = _open(tmp_path)
    alice = g.add_node(
        "alice",
        label="Person",
        labels=["Entity", "Person"],
        name="Alice",
        updated_by="alias",
    )
    assert alice.id == alias_record_id("alice")
    assert alice.labels == ("Person", "Entity")
    assert alice.props == {"name": "Alice"}
    assert alice.updated_by == "alias"
    got = g.get_node(alice.id)
    assert got is not None
    assert got.props["name"] == "Alice"
    assert got.labels == ("Person", "Entity")
    listed = list(g.iter_nodes())
    assert [n.id for n in listed] == [alice.id]
    assert listed[0].props["name"] == "Alice"
    assert list(g.nodes()) == [alice.id]
    assert g.has_node("alice")
    assert g.has_node(alice.id)


def test_add_node_replaces_whole_record(tmp_path: Path):
    g = _open(tmp_path)
    g.add_node("alice", label="Person", name="Alice", city="Paris")
    replaced = g.add_node("alice", label="Entity", name="Alicia")
    got = g.get_node(replaced.id)
    assert got is not None
    assert got.labels == ("Entity",)
    assert got.props == {"name": "Alicia"}
    assert list(g.iter_nodes()) == [got]


def test_add_edge_visible_via_primary_get_and_iter(tmp_path: Path):
    g = _open(tmp_path)
    alice = g.add_node("alice", label="Person", name="Alice")
    bob = g.add_node("bob", label="Person", name="Bob")
    edge = g.add_edge("alice", "bob", type="KNOWS", since=2020, updated_by="alias")
    assert edge.type == "KNOWS"
    assert edge.from_id == alice.id
    assert edge.to_id == bob.id
    assert edge.props == {"since": 2020}
    assert edge.updated_by == "alias"
    got = g.get_edge(edge.id)
    assert got is not None
    assert got.type == "KNOWS"
    assert got.props["since"] == 2020
    listed = list(g.iter_edges())
    assert [e.id for e in listed] == [edge.id]
    assert list(g.edges()) == [(alice.id, bob.id)]
    assert g.has_edge("alice", "bob")
    assert g.has_edge(alice.id, bob.id, type="KNOWS")
    assert not g.has_edge("bob", "alice")
    assert not g.has_edge("alice", "bob", type="LIKES")
    assert bob.id in list(g.neighbors("alice"))
    assert list(g.neighbors("bob")) == []


def test_add_edge_defaults_type_and_requires_endpoints(tmp_path: Path):
    g = _open(tmp_path)
    g.add_node("alice")
    with pytest.raises(ValueError, match="not a live node"):
        g.add_edge("alice", "bob")
    g.add_node("bob")
    edge = g.add_edge("alice", "bob")
    assert edge.type == DEFAULT_REL_TYPE == "RELATED"
    assert edge.props == {}
    assert g.get_edge(edge.id) is not None
    assert any(e.id == edge.id and e.type == "RELATED" for e in g.iter_edges())


def test_parallel_edges_neighbors_once_and_remove_one(tmp_path: Path):
    g = _open(tmp_path)
    alice = g.add_node("alice")
    bob = g.add_node("bob")
    knows = g.add_edge("alice", "bob", type="KNOWS", n=1)
    also = g.add_edge("alice", "bob", type="KNOWS", n=2)
    likes = g.add_edge("alice", "bob", type="LIKES")
    back = g.add_edge("bob", "alice", type="KNOWS")
    assert list(g.neighbors("alice")) == [bob.id]
    assert list(g.neighbors("bob")) == [alice.id]
    assert {e.id for e in g.iter_edges()} == {knows.id, also.id, likes.id, back.id}
    removed = g.remove_edge("alice", "bob", type="KNOWS")
    assert removed.id in {knows.id, also.id}
    assert g.get_edge(removed.id) is None
    live = list(g.iter_edges())
    assert len([e for e in live if e.type == "KNOWS" and e.from_id == alice.id]) == 1
    assert g.has_edge("alice", "bob", type="KNOWS")
    assert g.has_edge("alice", "bob", type="LIKES")
    assert g.get_edge(likes.id) is not None
    assert g.get_edge(back.id) is not None


def test_remove_node_and_edge_visible_via_primary_reads(tmp_path: Path):
    g = _open(tmp_path)
    alice = g.add_node(1, label="Num", name="one")
    bob = g.add_node(2, name="two")
    edge = g.add_edge(1, 2, type="KNOWS")
    assert g.get_node(alice.id).props["name"] == "one"
    assert g.has_node(1)
    assert not g.has_node(3)
    g.remove_edge(1, 2)
    assert g.get_edge(edge.id) is None
    assert list(g.iter_edges()) == []
    assert not g.has_edge(1, 2)
    with pytest.raises(KeyError):
        g.remove_edge(1, 2)
    g.remove_node(1)
    assert g.get_node(alice.id) is None
    assert g.get_node(bob.id) is not None
    assert [n.id for n in g.iter_nodes()] == [bob.id]
    assert not g.has_node(1)
    with pytest.raises(KeyError):
        g.remove_node(1)
    with pytest.raises(KeyError):
        list(g.neighbors(1))


def test_uuid_node_id_is_the_record_id(tmp_path: Path):
    g = _open(tmp_path)
    uid = "00000000-0000-4000-8000-00000000000a"
    rec = g.add_node(uid, name="fixed")
    assert rec.id == uid
    assert g.get_node(uid).props["name"] == "fixed"
    assert uid in list(g.nodes())
    assert any(n.props["name"] == "fixed" for n in g.iter_nodes())


def test_neighbors_reads_adjacency_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    g = _open(tmp_path)
    alice = g.add_node("alice")
    bob = g.add_node("bob")
    g.add_edge("alice", "bob", type="KNOWS")

    def _unexpected_scan():
        raise AssertionError("neighbors should use the local adjacency index")

    monkeypatch.setattr(g, "_graph_edges_unlocked", _unexpected_scan)
    assert list(g.neighbors("alice")) == [bob.id]


def test_self_loop_neighbor(tmp_path: Path):
    g = _open(tmp_path)
    node = g.add_node("a")
    edge = g.add_edge("a", "a", type="LOOP")
    assert list(g.neighbors("a")) == [node.id]
    assert g.has_edge("a", "a")
    assert g.get_edge(edge.id).type == "LOOP"


def test_remove_node_detaches_edges(tmp_path: Path):
    g = _open(tmp_path)
    alice = g.add_node("alice")
    bob = g.add_node("bob")
    edge = g.add_edge("alice", "bob", type="KNOWS")
    g.remove_node("alice")
    assert g.get_node(alice.id) is None
    assert g.get_node(bob.id) is not None
    assert g.get_edge(edge.id) is None
    assert list(g.iter_edges()) == []
    assert list(g.edges()) == []


def test_bad_label_and_relationship_type(tmp_path: Path):
    g = _open(tmp_path)
    with pytest.raises(TypeError):
        g.add_node("alice", label="")
    with pytest.raises(TypeError):
        g.add_node("alice", labels=["ok", ""])
    with pytest.raises(TypeError):
        g.add_node("alice", labels={"Person"})
    g.add_node("alice")
    g.add_node("bob")
    with pytest.raises(TypeError):
        g.add_edge("alice", "bob", type="")
    with pytest.raises(TypeError):
        g.has_edge("alice", "bob", type="")


def test_no_algorithm_surface():
    assert not hasattr(GraphStore, "shortest_path")
    assert not hasattr(GraphStore, "shortest_paths")
