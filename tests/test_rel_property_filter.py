"""Relationship property filters must not match every edge.

Graphiti's delete-by-group pattern uses an inline map on the relationship
(``MATCH ()-[r {group_id: $group_id}]->() DELETE r``). ``graphiti-core`` is
not installed in this repo, so these tests run that Cypher through
``GraphStore.execute``, which is what the in-process driver calls.
"""

from pathlib import Path

from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="rel-filter")
    return GraphStore.open(root, data_dir=tmp_path / "data")


def _two_groups(g: GraphStore) -> dict[str, list]:
    """Four edges in ``alpha`` and four in ``beta`` (eight total)."""
    edges: dict[str, list] = {}
    for gid in ("alpha", "beta"):
        edges[gid] = []
        for i in range(4):
            a = g.make_node(
                labels=["Entity"],
                props={"name": f"{gid}-a{i}", "group_id": gid},
            )
            b = g.make_node(
                labels=["Entity"],
                props={"name": f"{gid}-b{i}", "group_id": gid},
            )
            edges[gid].append(
                g.make_edge(
                    type="RELATES_TO",
                    from_id=a.id,
                    to_id=b.id,
                    props={"group_id": gid, "name": "KNOWS"},
                )
            )
    return edges


def test_match_rel_property_map_respects_group(tmp_path: Path):
    g = _open(tmp_path)
    edges = _two_groups(g)
    query = "MATCH ()-[r {group_id: $g}]->() RETURN r.group_id AS group_id"
    alpha = g.execute(query, {"g": "alpha"})
    assert len(alpha) == 4
    assert {row["group_id"] for row in alpha} == {"alpha"}
    assert g.execute(query, {"g": "no-such-group"}) == []
    # Anonymous map, a bound endpoint, and both keys in the map.
    anon = g.execute("MATCH ()-[{group_id: $g}]->() RETURN 1 AS ok", {"g": "beta"})
    assert len(anon) == 4
    start = edges["alpha"][0]
    walked = g.execute(
        """
        MATCH (a)-[r {group_id: $g}]->(b)
        WHERE a.uuid = $uuid
        RETURN r.group_id AS group_id
        """,
        {"g": "beta", "uuid": start.from_id},
    )
    assert walked == []
    both = g.execute(
        "MATCH ()-[r {group_id: $g, name: $name}]->() RETURN r.name AS name",
        {"g": "alpha", "name": "OTHER"},
    )
    assert both == []


def test_where_on_rel_var_filters(tmp_path: Path):
    g = _open(tmp_path)
    _two_groups(g)
    rows = g.execute(
        "MATCH ()-[r]->() WHERE r.group_id = $g RETURN r.group_id AS group_id",
        {"g": "beta"},
    )
    assert len(rows) == 4
    assert {row["group_id"] for row in rows} == {"beta"}
    assert (
        g.execute(
            "MATCH ()-[r]->() WHERE r.group_id = $g RETURN r.group_id AS group_id",
            {"g": "no-such-group"},
        )
        == []
    )


def test_optional_match_rel_property_map(tmp_path: Path):
    g = _open(tmp_path)
    _two_groups(g)
    rows = g.execute(
        """
        OPTIONAL MATCH ()-[r {group_id: $g}]->()
        RETURN r.group_id AS group_id
        """,
        {"g": "no-such-group"},
    )
    assert rows == [{"group_id": None}]
    hit = g.execute(
        """
        OPTIONAL MATCH ()-[r {group_id: $g}]->()
        RETURN r.group_id AS group_id
        """,
        {"g": "alpha"},
    )
    assert len(hit) == 4
    assert {row["group_id"] for row in hit} == {"alpha"}


def test_merge_rel_property_map_does_not_reuse_other_group(tmp_path: Path):
    g = _open(tmp_path)
    a = g.make_node(labels=["Entity"], props={"name": "a", "group_id": "alpha"})
    b = g.make_node(labels=["Entity"], props={"name": "b", "group_id": "alpha"})
    alpha = g.make_edge(
        type="RELATES_TO",
        from_id=a.id,
        to_id=b.id,
        props={"group_id": "alpha", "name": "KNOWS"},
    )
    beta = g.make_edge(
        type="RELATES_TO",
        from_id=a.id,
        to_id=b.id,
        props={"group_id": "beta", "name": "KNOWS"},
    )
    rows = g.execute(
        """
        MATCH (a {uuid: $a})
        MATCH (b {uuid: $b})
        MERGE (a)-[r:RELATES_TO {group_id: $g}]->(b)
        RETURN r.uuid AS uuid, r.group_id AS group_id
        """,
        {"a": a.id, "b": b.id, "g": "alpha"},
    )
    assert rows == [{"uuid": alpha.id, "group_id": "alpha"}]
    assert {edge.id for edge in g.iter_edges()} == {alpha.id, beta.id}


def test_delete_rel_by_property_leaves_other_group(tmp_path: Path):
    g = _open(tmp_path)
    edges = _two_groups(g)
    g.execute("MATCH ()-[r {group_id: $g}]->() DELETE r", {"g": "no-such-group"})
    assert {edge.id for edge in g.iter_edges()} == {
        edge.id for group in edges.values() for edge in group
    }
    g.execute("MATCH ()-[r]->() WHERE r.group_id = $g DELETE r", {"g": "alpha"})
    left = {edge.id for edge in g.iter_edges()}
    assert left == {edge.id for edge in edges["beta"]}
    alpha_nodes = g.execute("MATCH (n) WHERE n.group_id = 'alpha' RETURN n.name AS name")
    assert len(alpha_nodes) == 8


def test_detach_delete_node_by_property_leaves_other_group(tmp_path: Path):
    g = _open(tmp_path)
    edges = _two_groups(g)
    g.execute("MATCH (n {group_id: $g}) DETACH DELETE n", {"g": "no-such-group"})
    assert len(list(g.iter_nodes())) == 16
    assert len(list(g.iter_edges())) == 8
    g.execute("MATCH (n) WHERE n.group_id = $g DELETE n", {"g": "alpha"})
    names = {node.props.get("name") for node in g.iter_nodes()}
    assert names == {f"beta-a{i}" for i in range(4)} | {f"beta-b{i}" for i in range(4)}
    assert {edge.id for edge in g.iter_edges()} == {edge.id for edge in edges["beta"]}


def test_graphiti_delete_everything_by_group_id(tmp_path: Path):
    """Edge map delete, then node delete, scoped to one group.

    This is the delete-by-group shape Graphiti sends (inline ``{group_id}``
    on the relationship, then ``DETACH DELETE`` of matching nodes). The
    in-process driver forwards it to ``GraphStore.execute``.
    """
    g = _open(tmp_path)
    edges = _two_groups(g)
    g.execute(
        "MATCH ()-[r {group_id: $group_id}]->() DELETE r",
        {"group_id": "alpha"},
    )
    g.execute(
        "MATCH (n {group_id: $group_id}) DETACH DELETE n",
        {"group_id": "alpha"},
    )
    assert {node.props.get("group_id") for node in g.iter_nodes()} == {"beta"}
    assert {edge.id for edge in g.iter_edges()} == {edge.id for edge in edges["beta"]}
    g.execute(
        "MATCH ()-[r {group_id: $group_id}]->() DELETE r",
        {"group_id": "no-such-group"},
    )
    g.execute(
        "MATCH (n {group_id: $group_id}) DETACH DELETE n",
        {"group_id": "no-such-group"},
    )
    assert len(list(g.iter_edges())) == 4
    assert len(list(g.iter_nodes())) == 8
