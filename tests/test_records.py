import pytest

from graph_ted_db.store.records import (
    EdgeRecord,
    GraphMeta,
    NodeRecord,
    Tombstone,
    VectorRecord,
)


def test_node_roundtrip():
    rec = NodeRecord.from_dict(
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "v": 1,
            "updated_at": "2026-08-25T15:04:05Z",
            "updated_by": "alice",
            "labels": ["Entity"],
            "props": {"name": "Kamala Harris"},
        }
    )
    again = NodeRecord.from_dict(rec.to_dict())
    assert again.id == rec.id
    assert again.labels == ("Entity",)
    assert again.props["name"] == "Kamala Harris"


def test_edge_uses_from_to_fields():
    rec = EdgeRecord.from_dict(
        {
            "id": "1b4e28ba-2fa1-11d2-883f-b9a761bde3fb",
            "updated_at": "2026-08-25T15:04:05.000000Z",
            "type": "RELATES_TO",
            "from": "550e8400-e29b-41d4-a716-446655440000",
            "to": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "props": {},
        }
    )
    dumped = rec.to_dict()
    assert dumped["from"] == rec.from_id
    assert dumped["to"] == rec.to_id
    assert "from_id" not in dumped


def test_vector_rejects_json_float_array():
    with pytest.raises(ValueError, match="base64"):
        VectorRecord.from_dict(
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "updated_at": "2026-08-25T15:04:05Z",
                "dim": 2,
                "vec": [0.1, 0.2],
            },
            property="name_embedding",
        )


def test_vector_pack_unpack():
    rec = VectorRecord.from_floats(
        id="550e8400-e29b-41d4-a716-446655440000",
        property="name_embedding",
        values=[1.0, -2.5, 0.0],
        updated_at="2026-08-25T15:04:05Z",
    )
    assert rec.dim == 3
    assert rec.dtype == "f32le"
    unpacked = rec.floats()
    assert unpacked[0] == pytest.approx(1.0)
    assert unpacked[1] == pytest.approx(-2.5)
    again = VectorRecord.from_dict(rec.to_dict(), property="name_embedding")
    assert again.floats() == unpacked


def test_graph_meta_rejects_unknown_format():
    with pytest.raises(ValueError, match="unsupported format"):
        GraphMeta.from_dict(
            {
                "format": "neo4j",
                "format_version": 1,
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "x",
                "created_at": "2026-08-25T15:04:05Z",
                "shard_fanout": 256,
            }
        )


def test_graph_meta_preserves_unknown_fields():
    meta = GraphMeta.from_dict(
        {
            "format": "graph-ted-db",
            "format_version": 1,
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "name": "x",
            "created_at": "2026-08-25T15:04:05Z",
            "shard_fanout": 256,
            "embedder_model": "text-embedding-3-large",
            "embedder_dim": 1024,
        }
    )
    dumped = meta.to_dict()
    assert dumped["embedder_model"] == "text-embedding-3-large"
    assert dumped["embedder_dim"] == 1024
    assert dumped["format"] == "graph-ted-db"


def test_graph_meta_rejects_newer_version():
    with pytest.raises(ValueError, match="newer"):
        GraphMeta.from_dict(
            {
                "format": "graph-ted-db",
                "format_version": 99,
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "x",
                "created_at": "2026-08-25T15:04:05Z",
                "shard_fanout": 256,
            }
        )


def test_tombstone_vector_requires_property():
    with pytest.raises(ValueError):
        Tombstone.from_dict(
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "kind": "vector",
                "updated_at": "2026-08-25T15:04:05Z",
            }
        )
    ts = Tombstone.from_dict(
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "kind": "vector",
            "property": "name_embedding",
            "updated_at": "2026-08-25T15:04:05Z",
        }
    )
    assert ts.to_dict()["property"] == "name_embedding"
