from pathlib import Path

import pytest

from graph_ted_db.engine import CypherError, parse_query
from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="cy")
    return GraphStore.open(root, data_dir=tmp_path / "data")


def test_parse_match_return():
    q = parse_query("MATCH (n:Entity) RETURN n.name AS name")
    assert len(q.clauses) == 2


def test_trim_name_lookup(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "TedCo", "group_id": "default"})
    rows = g.execute(
        """
        MATCH (n:Entity)
        WHERE toLower(trim(toString(n.name))) = $name
          AND n.group_id IN $group_ids
        RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary,
               labels(n) AS labels, n.group_id AS group_id, n.created_at AS created_at,
               n.attributes AS attributes
        ORDER BY n.created_at DESC
        LIMIT $limit
        """,
        {"name": "tedco", "group_ids": ["default"], "limit": 10},
    )
    assert rows[0]["name"] == "TedCo"
    assert rows[0]["summary"] == ""
    assert rows[0]["created_at"]
    assert rows[0]["attributes"] == {} or isinstance(rows[0]["attributes"], dict)


def test_limit_and_union_all(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "Ted", "group_id": "default"})
    g.make_node(labels=["Entity"], props={"name": "Ann", "group_id": "sales"})
    rows = g.execute(
        """
        MATCH (n) WHERE n.group_id IS NOT NULL
        RETURN DISTINCT 'node' AS entity, n.group_id AS group_id
        LIMIT $node_limit
        UNION ALL
        MATCH ()-[r]-() WHERE r.group_id IS NOT NULL
        RETURN DISTINCT 'relationship' AS entity, r.group_id AS group_id
        LIMIT $relationship_limit
        """,
        {"node_limit": 10, "relationship_limit": 10},
    )
    ids = {row["group_id"] for row in rows}
    assert ids == {"default", "sales"}
    assert all(row["entity"] == "node" for row in rows)


def test_fulltext_query_nodes(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(
        labels=["Entity"],
        props={"name": "Ted", "summary": "a person", "group_id": "default"},
    )
    g.make_node(
        labels=["Entity"],
        props={"name": "Ann", "summary": "another", "group_id": "default"},
    )
    rows = g.execute(
        """
        CALL db.index.fulltext.queryNodes("node_name_and_summary", $query, {limit: $limit})
        YIELD node AS n, score
        RETURN n.name AS name, score
        ORDER BY score DESC
        LIMIT $limit
        """,
        {"query": "Ted", "limit": 5},
    )
    assert rows
    assert rows[0]["name"] == "Ted"


def test_graphiti_search_return_fills_required_fields(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "Mavis", "group_id": "default"})
    rows = g.execute(
        """
        CALL db.index.fulltext.queryNodes("node_name_and_summary", $query, {limit: $limit})
        YIELD node AS n, score
        WITH n, score
        ORDER BY score DESC
        LIMIT $limit
        RETURN
            n.uuid AS uuid,
            n.name AS name,
            n.group_id AS group_id,
            n.created_at AS created_at,
            n.summary AS summary,
            labels(n) AS labels,
            properties(n) AS attributes
        """,
        {"query": "Mavis", "limit": 10},
    )
    assert rows[0]["name"] == "Mavis"
    assert rows[0]["created_at"]
    assert rows[0]["summary"] == ""
    assert rows[0]["attributes"] != {}


def test_entity_return_matches_graphiti_shape(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "Ted", "group_id": "default"})
    rows = g.execute(
        """
        MATCH (n:Entity)
        WHERE n.name = $name
        RETURN
            n.uuid AS uuid,
            n.name AS name,
            n.group_id AS group_id,
            n.created_at AS created_at,
            n.summary AS summary,
            labels(n) AS labels,
            properties(n) AS attributes
        """,
        {"name": "Ted"},
    )
    assert rows[0]["name"] == "Ted"
    assert rows[0]["group_id"] == "default"
    assert isinstance(rows[0]["created_at"], str) and rows[0]["created_at"]
    assert rows[0]["summary"] == ""
    assert "Entity" in rows[0]["labels"]
    assert isinstance(rows[0]["attributes"], dict)


def test_datetime_jsonish_on_return(tmp_path: Path):
    from datetime import datetime, timezone

    g = _open(tmp_path)
    now = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    g.make_node(
        labels=["Episodic"],
        props={"name": "ep", "valid_at": now.isoformat(), "group_id": "default"},
    )
    rows = g.execute(
        """
        MATCH (e:Episodic)
        WHERE e.valid_at <= $reference_time
        RETURN e.name AS name, e.valid_at AS valid_at
        ORDER BY e.valid_at DESC
        LIMIT $num_episodes
        """,
        {"reference_time": now, "num_episodes": 10},
    )
    assert rows[0]["name"] == "ep"
    assert isinstance(rows[0]["valid_at"], str)


def test_unknown_function_mentions_registry(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "x"})
    with pytest.raises(CypherError, match="register"):
        g.execute("MATCH (n) RETURN bogus(n) AS x")


def test_match_by_label_and_property(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity", "Person"], props={"name": "Alice", "group_id": "default"})
    g.make_node(labels=["Entity"], props={"name": "Other", "group_id": "other"})
    rows = g.execute(
        "MATCH (n:Entity {group_id: $group_id}) RETURN n.name AS name ORDER BY name",
        {"group_id": "default"},
    )
    assert rows == [{"name": "Alice"}]


def test_optional_match_and_collect(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(labels=["Entity"], props={"name": "a", "group_id": "g"})
    b = g.make_node(labels=["Entity", "Form"], props={"name": "form", "group_id": "g"})
    g.make_edge(type="RELATES_TO", from_id=a.id, to_id=b.id, props={"name": "HAS_FORM"})
    rows = g.execute(
        """
        MATCH (n:Entity {group_id: $gid})
        WHERE NOT n:Form
        OPTIONAL MATCH (n)-[r]-(form:Entity {group_id: $gid})
        WHERE 'Form' IN labels(form)
        WITH n, collect(DISTINCT form.name) AS forms
        RETURN n.name AS name, forms
        """,
        {"gid": "g"},
    )
    assert rows == [{"name": "a", "forms": ["form"]}]


def test_list_and_map_subscript(tmp_path: Path):
    g = _open(tmp_path)
    rows = g.execute(
        "UNWIND $pairs AS pair RETURN pair[0] AS left, pair[1] AS right",
        {"pairs": [["a", "b"], ["c", "d"]]},
    )
    assert rows == [{"left": "a", "right": "b"}, {"left": "c", "right": "d"}]
    rows = g.execute("RETURN $items[9] AS missing", {"items": ["only"]})
    assert rows == [{"missing": None}]
    rows = g.execute("RETURN $bag['name'] AS name", {"bag": {"name": "Ted"}})
    assert rows == [{"name": "Ted"}]


def test_graphiti_duplicate_of_tuple_index(tmp_path: Path):
    """Graphiti filter_existing_duplicate_of_edges uses duplicate_tuple[0]/[1]."""
    g = _open(tmp_path)
    src = "00000000-0000-4000-8000-0000000000a1"
    dst = "00000000-0000-4000-8000-0000000000a2"
    g.make_node(
        record_id=src,
        labels=["Entity"],
        props={"uuid": src, "name": "Bob", "group_id": "default"},
    )
    g.make_node(
        record_id=dst,
        labels=["Entity"],
        props={"uuid": dst, "name": "Robert", "group_id": "default"},
    )
    g.make_edge(
        type="RELATES_TO",
        from_id=src,
        to_id=dst,
        props={"name": "IS_DUPLICATE_OF", "uuid": "00000000-0000-4000-8000-000000000099"},
    )
    rows = g.execute(
        """
        UNWIND $duplicate_node_uuids AS duplicate_tuple
        MATCH (n:Entity {uuid: duplicate_tuple[0]})-[r:RELATES_TO {name: 'IS_DUPLICATE_OF'}]->(m:Entity {uuid: duplicate_tuple[1]})
        RETURN DISTINCT
            n.uuid AS source_uuid,
            m.uuid AS target_uuid
        """,
        {"duplicate_node_uuids": [[src, dst], ["missing", dst]]},
    )
    assert rows == [{"source_uuid": src, "target_uuid": dst}]


def test_unwind_and_case(tmp_path: Path):
    g = _open(tmp_path)
    rows = g.execute(
        """
        UNWIND $uuids AS x
        RETURN CASE WHEN x = 'a' THEN 1 ELSE 0 END AS flag
        ORDER BY flag DESC
        """,
        {"uuids": ["a", "b"]},
    )
    assert rows == [{"flag": 1}, {"flag": 0}]


def test_detach_delete_via_cypher(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(
        record_id="00000000-0000-4000-8000-0000000000a1",
        labels=["Entity"],
        props={"uuid": "00000000-0000-4000-8000-0000000000a1", "name": "a", "group_id": "g"},
    )
    b = g.make_node(
        record_id="00000000-0000-4000-8000-0000000000b1",
        labels=["Entity"],
        props={"uuid": "00000000-0000-4000-8000-0000000000b1", "name": "b", "group_id": "g"},
    )
    g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id)
    rows = g.execute(
        """
        MATCH (n:Entity {uuid: $uuid, group_id: $gid})
        WITH n, n.uuid AS deleted_uuid, n.name AS deleted_name
        DETACH DELETE n
        RETURN deleted_uuid AS uuid, deleted_name AS name, 'entity' AS kind
        """,
        {"uuid": a.id, "gid": "g"},
    )
    assert rows == [{"uuid": a.id, "name": "a", "kind": "entity"}]
    assert g.get_node(a.id) is None
    assert list(g.iter_edges()) == []
    assert g.get_node(b.id) is not None


def test_delete_rel_by_element_id(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(labels=["Entity"], props={"name": "a"})
    b = g.make_node(labels=["Entity"], props={"name": "b"})
    edge = g.make_edge(
        type="RELATES_TO",
        from_id=a.id,
        to_id=b.id,
        props={"uuid": "00000000-0000-4000-8000-000000000099", "name": "KNOWS"},
        record_id="00000000-0000-4000-8000-000000000099",
    )
    rows = g.execute(
        """
        MATCH ()-[r]-()
        WHERE r.uuid = $uuid OR elementId(r) = $uuid
        WITH r, coalesce(r.uuid, elementId(r)) AS deleted_uuid, coalesce(r.name, type(r)) AS deleted_name
        DELETE r
        RETURN deleted_uuid AS uuid, deleted_name AS name, true AS deleted
        """,
        {"uuid": edge.id},
    )
    assert len(rows) == 1
    assert rows[0]["uuid"] == edge.id
    assert rows[0]["deleted"] is True
    assert g.get_edge(edge.id) is None
