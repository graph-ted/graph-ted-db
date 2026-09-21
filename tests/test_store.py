from pathlib import Path

from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.format import format_timestamp, shard_id
from graph_ted_db.store.records import EdgeRecord, NodeRecord, VectorRecord


def _open(tmp_path: Path, name: str = "g") -> GraphStore:
    root = tmp_path / name
    init_graph(root, name="demo")
    return GraphStore.open(root, data_dir=tmp_path / "locks")


def test_put_get_list_node(tmp_path: Path):
    g = _open(tmp_path)
    rec = g.make_node(labels=["Entity"], props={"name": "Alice"}, updated_by="test")
    got = g.get_node(rec.id)
    assert got is not None
    assert got.props["name"] == "Alice"
    assert got.labels == ("Entity",)
    listed = list(g.iter_nodes())
    assert [n.id for n in listed] == [rec.id]
    shard = g.paths.node_shard(rec.id)
    assert shard.is_file()
    assert shard.parent == g.paths.nodes_dir


def test_lock_file_is_outside_graph_folder(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "x"})
    assert not (g.root / "LOCK").exists()
    assert g._lock_path.is_relative_to(tmp_path / "locks")
    assert g._lock_path.is_file()


def test_same_shard_two_ids_both_survive(tmp_path: Path):
    g = _open(tmp_path)
    # Force both into shard 00.
    a = g.make_node(
        record_id="00000000-0000-4000-8000-000000000001",
        props={"name": "a"},
    )
    b = g.make_node(
        record_id="00000000-0000-4000-8000-000000000002",
        props={"name": "b"},
    )
    assert shard_id(a.id) == shard_id(b.id) == "00"
    names = {n.props["name"] for n in g.iter_nodes()}
    assert names == {"a", "b"}


def test_later_put_wins_same_id(tmp_path: Path):
    g = _open(tmp_path)
    rec_id = "550e8400-e29b-41d4-a716-446655440000"
    g.make_node(record_id=rec_id, props={"name": "old"})
    g.make_node(record_id=rec_id, props={"name": "new"})
    got = g.get_node(rec_id)
    assert got is not None
    assert got.props["name"] == "new"
    # Shard still has both lines until compact.
    text = g.paths.node_shard(rec_id).read_text(encoding="utf-8")
    assert "old" in text and "new" in text
    g.compact()
    text = g.paths.node_shard(rec_id).read_text(encoding="utf-8")
    assert "old" not in text
    assert "new" in text
    assert g.get_node(rec_id).props["name"] == "new"


def test_conflict_copy_union_merge(tmp_path: Path):
    g = _open(tmp_path)
    alice_id = "00000000-0000-4000-8000-00000000000a"
    bob_id = "00000000-0000-4000-8000-00000000000b"
    g.make_node(record_id=alice_id, props={"name": "Alice"})
    # Simulate OneDrive forking the shard while another user wrote Bob.
    canonical = g.paths.nodes_dir / "00.jsonl"
    conflict = g.paths.nodes_dir / "00-DESKTOP-conflict-2026-08-25.jsonl"
    bob = NodeRecord(
        id=bob_id,
        updated_at=format_timestamp(),
        labels=("Entity",),
        props={"name": "Bob"},
        updated_by="other",
    )
    conflict.write_text(bob.to_jsonl() + "\n", encoding="utf-8")
    assert canonical.is_file()
    names = {n.props["name"] for n in g.iter_nodes()}
    assert names == {"Alice", "Bob"}
    assert g.get_node(bob_id) is not None


def test_conflict_copy_same_id_newer_wins(tmp_path: Path):
    g = _open(tmp_path)
    rec_id = "00000000-0000-4000-8000-00000000000c"
    g.put_node(
        NodeRecord(
            id=rec_id,
            updated_at="2026-08-25T12:00:00.000000Z",
            labels=("Entity",),
            props={"name": "local"},
        )
    )
    conflict = g.paths.nodes_dir / "00 (conflicted copy).jsonl"
    remote = NodeRecord(
        id=rec_id,
        updated_at="2026-08-25T13:00:00.000000Z",
        labels=("Entity",),
        props={"name": "remote"},
    )
    conflict.write_text(remote.to_jsonl() + "\n", encoding="utf-8")
    got = g.get_node(rec_id)
    assert got is not None
    assert got.props["name"] == "remote"


def test_delete_and_resurrect(tmp_path: Path):
    g = _open(tmp_path)
    rec = g.make_node(props={"name": "gone"})
    g.delete_node(rec.id, updated_by="test")
    assert g.get_node(rec.id) is None
    assert list(g.iter_nodes()) == []
    again = g.make_node(record_id=rec.id, props={"name": "back"})
    got = g.get_node(rec.id)
    assert got is not None
    assert got.props["name"] == "back"
    assert again.id == rec.id


def test_edges_and_vectors(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(props={"name": "a"})
    b = g.make_node(props={"name": "b"})
    edge = g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id, props={"since": 2020})
    assert g.get_edge(edge.id).type == "KNOWS"
    assert {e.type for e in g.iter_edges()} == {"KNOWS"}
    vec = VectorRecord.from_floats(
        id=a.id,
        property="name_embedding",
        values=[0.1, 0.2, 0.3],
        updated_at=format_timestamp(),
    )
    g.put_vector(vec)
    got = g.get_vector("name_embedding", a.id)
    assert got is not None
    assert got.dim == 3
    g.delete_vector("name_embedding", a.id)
    assert g.get_vector("name_embedding", a.id) is None
    g.delete_edge(edge.id)
    assert g.get_edge(edge.id) is None


def test_delete_node_detaches_incident_edges(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(props={"name": "a"})
    b = g.make_node(props={"name": "b"})
    edge = g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id)
    g.delete_node(a.id)
    assert g.get_node(a.id) is None
    assert g.get_node(b.id) is not None
    assert g.get_edge(edge.id) is None
    assert list(g.iter_edges()) == []


def test_put_edge_requires_live_endpoints(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(props={"name": "a"})
    missing = "00000000-0000-4000-8000-0000000000ff"
    try:
        g.make_edge(type="KNOWS", from_id=a.id, to_id=missing)
    except ValueError as exc:
        assert "to_id" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing to_id")


def test_dangling_edge_is_hidden_then_doctor_tombstones(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(
        record_id="00000000-0000-4000-8000-00000000000a",
        props={"name": "a"},
    )
    # Bypass put_edge so we can plant a dangling RELATES_TO.
    dangling = EdgeRecord(
        id="00000000-0000-4000-8000-0000000000e1",
        updated_at=format_timestamp(),
        type="RELATES_TO",
        from_id=a.id,
        to_id="00000000-0000-4000-8000-0000000000ff",
        props={},
    )
    g.paths.edge_shard(dangling.id).write_text(dangling.to_jsonl() + "\n", encoding="utf-8")
    assert g.get_edge(dangling.id) is None
    assert list(g.iter_edges()) == []
    report = g.doctor()
    assert any(dangling.id in item for item in report.dangling_edges_tombstoned)
    assert g.get_edge(dangling.id) is None


def test_open_repairs_torn_shard_and_next_put_survives(tmp_path: Path):
    g = _open(tmp_path)
    rec = g.make_node(
        record_id="00000000-0000-4000-8000-000000000001",
        props={"name": "ok"},
    )
    shard = g.paths.node_shard(rec.id)
    shard.write_bytes(shard.read_bytes() + b'{"id":"torn-incomplete"')
    g2 = GraphStore.open(g.root, data_dir=tmp_path / "locks")
    assert g2.get_node(rec.id) is not None
    other = g2.make_node(
        record_id="00000000-0000-4000-8000-000000000002",
        props={"name": "also"},
    )
    names = {n.props["name"] for n in g2.iter_nodes()}
    assert names == {"ok", "also"}
    assert other.id.endswith("0002")
    text = shard.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "torn-incomplete" not in text


def test_open_removes_leftover_compact_tmp(tmp_path: Path):
    g = _open(tmp_path)
    rec = g.make_node(
        record_id="00000000-0000-4000-8000-000000000001",
        props={"name": "x"},
    )
    tmp = g.paths.node_shard(rec.id).with_name("00.jsonl.tmp")
    tmp.write_text("leftover\n", encoding="utf-8")
    GraphStore.open(g.root, data_dir=tmp_path / "locks")
    assert not tmp.exists()


def test_corrupt_labels_json_does_not_block_open(tmp_path: Path):
    g = _open(tmp_path)
    g.paths.labels_json.write_text("{not json", encoding="utf-8")
    g2 = GraphStore.open(g.root, data_dir=tmp_path / "locks")
    rec = g2.make_node(labels=["Entity"], props={"name": "after"})
    assert rec.props["name"] == "after"


def test_skips_invalid_jsonl_line(tmp_path: Path):
    g = _open(tmp_path)
    rec = g.make_node(props={"name": "ok"})
    shard = g.paths.node_shard(rec.id)
    shard.write_text(
        shard.read_text(encoding="utf-8") + "this is not json\n",
        encoding="utf-8",
    )
    assert g.get_node(rec.id) is not None
    assert g.skipped_lines  # invalid line recorded
