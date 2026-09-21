from pathlib import Path

from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="idx")
    return GraphStore.open(root, data_dir=tmp_path / "data")


def test_index_tracks_nodes_edges_and_adj(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(labels=["Entity"], props={"name": "a", "uuid": None})
    b = g.make_node(labels=["Entity", "Person"], props={"name": "b"})
    edge = g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id)
    idx = g._index
    assert idx is not None
    assert a.id in idx.nodes
    assert b.id in idx.nodes
    assert edge.id in idx.edges
    neigh = idx.neighbors(a.id, rel_type="KNOWS", direction="out")
    assert len(neigh) == 1
    assert neigh[0][1].id == b.id
    catalog = idx.directory / "catalog.jsonl"
    assert catalog.is_file()
    assert idx.directory.is_relative_to(tmp_path / "data")
    assert not (g.root / "index").exists()


def test_cypher_sees_writes_from_another_open_of_same_folder(tmp_path: Path):
    """CLI put-node is a second process; serve must pick it up without restart."""
    data = tmp_path / "data"
    root = tmp_path / "g"
    init_graph(root, name="idx")
    server = GraphStore.open(root, data_dir=data)
    assert server.execute("MATCH (n) RETURN n.name AS name") == []
    writer = GraphStore.open(root, data_dir=data)
    writer.make_node(labels=["Entity"], props={"name": "from-cli"})
    rows = server.execute("MATCH (n) RETURN n.name AS name")
    assert rows == [{"name": "from-cli"}]


def test_index_drops_deleted_node_and_edges(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(labels=["Entity"], props={"name": "a"})
    b = g.make_node(labels=["Entity"], props={"name": "b"})
    edge = g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id)
    g.delete_node(a.id)
    idx = g._index
    assert idx is not None
    assert a.id not in idx.nodes
    assert edge.id not in idx.edges
    assert idx.neighbors(b.id) == []
