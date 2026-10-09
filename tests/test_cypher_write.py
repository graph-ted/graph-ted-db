from pathlib import Path

from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="w")
    return GraphStore.open(root, data_dir=tmp_path / "data")


def test_execute_many_commits_all(tmp_path: Path):
    g = _open(tmp_path)
    g.execute_many(
        [
            (
                "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name SET n.group_id = $gid "
                "RETURN n.name AS name",
                {
                    "uuid": "00000000-0000-4000-8000-0000000000a1",
                    "name": "One",
                    "gid": "default",
                },
            ),
            (
                "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name SET n.group_id = $gid "
                "RETURN n.name AS name",
                {
                    "uuid": "00000000-0000-4000-8000-0000000000a2",
                    "name": "Two",
                    "gid": "default",
                },
            ),
        ]
    )
    names = {row["name"] for row in g.execute("MATCH (n:Entity) RETURN n.name AS name")}
    assert names == {"One", "Two"}


def test_execute_many_rolls_back_on_error(tmp_path: Path):
    g = _open(tmp_path)
    try:
        g.execute_many(
            [
                (
                    "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name SET n.group_id = $gid "
                    "RETURN n.name AS name",
                    {
                        "uuid": "00000000-0000-4000-8000-0000000000b1",
                        "name": "Temp",
                        "gid": "default",
                    },
                ),
                ("MATCH (n) RETURN bogus(n) AS x", {}),
            ]
        )
    except Exception:
        pass
    else:
        raise AssertionError("expected cypher error")
    leftover = g.execute(
        "MATCH (n:Entity {name: $name}) RETURN n.name AS name",
        {"name": "Temp"},
    )
    assert leftover == []


def test_execute_many_mentions_edge_after_uncommitted_nodes(tmp_path: Path):
    """Graphiti add_nodes_and_edges_bulk: nodes then MENTIONS in one transaction."""
    g = _open(tmp_path)
    episode = "00000000-0000-4000-8000-0000000000e1"
    entity = "00000000-0000-4000-8000-0000000000a1"
    edge = "00000000-0000-4000-8000-000000000011"
    g.execute_many(
        [
            (
                "MERGE (n:Episodic {uuid: $uuid}) SET n.name = $name SET n.group_id = $gid "
                "RETURN n.uuid AS uuid",
                {"uuid": episode, "name": "Organization TedCo", "gid": "default"},
            ),
            (
                "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name SET n.group_id = $gid "
                "RETURN n.uuid AS uuid",
                {"uuid": entity, "name": "TedCo", "gid": "default"},
            ),
            (
                "MATCH (episode:Episodic {uuid: $episode_uuid}) "
                "MATCH (node:Entity {uuid: $entity_uuid}) "
                "MERGE (episode)-[e:MENTIONS {uuid: $uuid}]->(node) "
                "RETURN e.uuid AS uuid",
                {
                    "episode_uuid": episode,
                    "entity_uuid": entity,
                    "uuid": edge,
                },
            ),
        ]
    )
    rows = g.execute(
        "MATCH (e:Episodic)-[:MENTIONS]->(n:Entity) RETURN e.name AS episode, n.name AS org"
    )
    assert rows == [{"episode": "Organization TedCo", "org": "TedCo"}]


def test_merge_creates_and_sets(tmp_path: Path):
    g = _open(tmp_path)
    nid = "00000000-0000-4000-8000-0000000000aa"
    rows = g.execute(
        """
        MERGE (n:Entity {uuid: $uuid})
        SET n:Person
        SET n.name = $name
        SET n.group_id = $gid
        RETURN n.uuid AS uuid, n.name AS name
        """,
        {"uuid": nid, "name": "Alice", "gid": "default"},
    )
    assert rows[0]["uuid"] == nid
    assert rows[0]["name"] == "Alice"
    again = g.execute(
        "MATCH (n:Entity {uuid: $uuid}) RETURN n.name AS name, labels(n) AS labels",
        {"uuid": nid},
    )
    assert again[0]["name"] == "Alice"
    assert "Person" in again[0]["labels"]
    assert "Entity" in again[0]["labels"]


def test_merge_is_idempotent(tmp_path: Path):
    g = _open(tmp_path)
    nid = "00000000-0000-4000-8000-0000000000bb"
    g.execute(
        "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name RETURN n.uuid AS uuid",
        {"uuid": nid, "name": "first"},
    )
    g.execute(
        "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name RETURN n.uuid AS uuid",
        {"uuid": nid, "name": "second"},
    )
    rows = g.execute("MATCH (n:Entity) RETURN n.name AS name")
    assert rows == [{"name": "second"}]


def test_create_index_is_noop(tmp_path: Path):
    g = _open(tmp_path)
    assert g.execute("CREATE INDEX entity_uuid IF NOT EXISTS FOR (n:Entity) ON (n.uuid)") == []


def test_set_attributes_json(tmp_path: Path):
    g = _open(tmp_path)
    nid = "00000000-0000-4000-8000-0000000000cc"
    g.execute(
        "MERGE (n:Entity {uuid: $uuid}) SET n.name = 'x' RETURN n.uuid AS uuid",
        {"uuid": nid},
    )
    g.execute(
        "MATCH (n:Entity {uuid: $uuid}) SET n.attributes = $attributes RETURN n.uuid AS uuid",
        {"uuid": nid, "attributes": '{"role":"buyer"}'},
    )
    rows = g.execute(
        "MATCH (n:Entity {uuid: $uuid}) RETURN n.attributes AS attributes",
        {"uuid": nid},
    )
    assert rows[0]["attributes"] == '{"role":"buyer"}'


def test_unwind_merge_bulk(tmp_path: Path):
    g = _open(tmp_path)
    rows = g.execute(
        """
        UNWIND $nodes AS node
        MERGE (n:Entity {uuid: node.uuid})
        SET n:$(node.labels)
        SET n = node
        RETURN n.uuid AS uuid
        """,
        {
            "nodes": [
                {
                    "uuid": "00000000-0000-4000-8000-0000000000d1",
                    "name": "A",
                    "group_id": "default",
                    "labels": ["Entity", "Person"],
                },
                {
                    "uuid": "00000000-0000-4000-8000-0000000000d2",
                    "name": "B",
                    "group_id": "default",
                    "labels": ["Entity"],
                },
            ]
        },
    )
    assert {r["uuid"] for r in rows} == {
        "00000000-0000-4000-8000-0000000000d1",
        "00000000-0000-4000-8000-0000000000d2",
    }
    names = {r["name"] for r in g.execute("MATCH (n:Entity) RETURN n.name AS name")}
    assert names == {"A", "B"}


def test_merge_mentions_edge(tmp_path: Path):
    g = _open(tmp_path)
    ep = "00000000-0000-4000-8000-0000000000e1"
    ent = "00000000-0000-4000-8000-0000000000a1"
    edge = "00000000-0000-4000-8000-000000000011"
    g.execute(
        "MERGE (n:Episodic {uuid: $uuid}) SET n.name = 'ep' RETURN n.uuid AS uuid",
        {"uuid": ep},
    )
    g.execute(
        "MERGE (n:Entity {uuid: $uuid}) SET n.name = 'ent' RETURN n.uuid AS uuid",
        {"uuid": ent},
    )
    g.execute(
        """
        MATCH (episode:Episodic {uuid: $episode_uuid})
        MATCH (node:Entity {uuid: $entity_uuid})
        MERGE (episode)-[e:MENTIONS {uuid: $uuid}]->(node)
        SET e.group_id = $group_id
        RETURN e.uuid AS uuid
        """,
        {
            "episode_uuid": ep,
            "entity_uuid": ent,
            "uuid": edge,
            "group_id": "default",
        },
    )
    rows = g.execute(
        "MATCH (a)-[r:MENTIONS]->(b) RETURN a.name AS from, b.name AS to, type(r) AS type"
    )
    assert rows == [{"from": "ep", "to": "ent", "type": "MENTIONS"}]
