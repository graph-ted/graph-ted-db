"""Vector search through serve, the way GraphTedDbDriver.execute_query calls it.

Graphiti (Neo4j dialect) saves with ``SET n = $entity_data`` plus
``CALL db.create.setNodeVectorProperty`` and searches with
``vector.similarity.cosine(n.name_embedding, $search_vector)``. The driver
posts that Cypher to ``/cypher`` with ``max_records=0``.
"""

import threading
from contextlib import contextmanager
from pathlib import Path

from graph_ted_db.driver.http import post_cypher
from graph_ted_db.server import make_server
from graph_ted_db.store import GraphStore, PublishPolicy, init_graph

ADA = "00000000-0000-4000-8000-0000000000a1"
GRACE = "00000000-0000-4000-8000-0000000000a2"
EDGE = "00000000-0000-4000-8000-0000000000e1"

# Graphiti's Neo4j entity save. The embedding rides on the map and is also
# passed to setNodeVectorProperty.
_SAVE_NODE = """
MERGE (n:Entity {uuid: $entity_data.uuid})
SET n:Person
SET n = $entity_data
WITH n
CALL db.create.setNodeVectorProperty(n, "name_embedding", $entity_data.name_embedding)
RETURN n.uuid AS uuid
"""

_SAVE_EDGE = """
MATCH (a:Entity {uuid: $source})
MATCH (b:Entity {uuid: $target})
MERGE (a)-[e:RELATES_TO {uuid: $uuid}]->(b)
SET e = $edge_data
WITH e
CALL db.create.setRelationshipVectorProperty(e, "fact_embedding", $edge_data.fact_embedding)
RETURN e.uuid AS uuid
"""

# graphiti_core node_similarity_search for GraphProvider.NEO4J.
_SEARCH_NODES = """
MATCH (n:Entity)
WHERE n.group_id IN $group_ids
WITH n, vector.similarity.cosine(n.name_embedding, $search_vector) AS score
WHERE score > $min_score
RETURN
    n.uuid AS uuid,
    n.name AS name,
    n.group_id AS group_id,
    n.created_at AS created_at,
    n.summary AS summary,
    labels(n) AS labels,
    properties(n) AS attributes
ORDER BY score DESC
LIMIT $limit
"""

_SEARCH_EDGES = """
MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
WHERE e.group_id IN $group_ids
WITH e, vector.similarity.cosine(e.fact_embedding, $search_vector) AS score
WHERE score > $min_score
RETURN e.uuid AS uuid, score
ORDER BY score DESC
LIMIT $limit
"""

_READ_NODE = """
MATCH (n:Entity {uuid: $uuid})
RETURN n.name_embedding AS name_embedding, n.name AS name
"""

_READ_EDGE = """
MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
RETURN e.fact_embedding AS fact_embedding
"""


def _axis(dim: int, index: int) -> list[float]:
    values = [0.0] * dim
    values[index] = 1.0
    return values


def _entity(uuid: str, name: str, embedding: list[float]) -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "group_id": "default",
        "summary": name,
        "created_at": "2026-10-25T00:00:00Z",
        "labels": ["Entity", "Person"],
        "name_embedding": embedding,
    }


@contextmanager
def _serve(store: GraphStore):
    server = make_server(store, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{int(port)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_vector_search_reads_embeddings_back(tmp_path: Path):
    """768-d name vectors and a 64-d fact vector survive serve, then a bundle seal.

    Install-testing against 0.1.1 returned 0 hits and empty embeddings from
    queries while vectors/**/*.jsonl on disk held the floats and text search
    still worked. The driver path is POST /cypher, max_records=0.
    """
    root = tmp_path / "g"
    init_graph(root, name="vectors")
    # No background seal: the first search reads the active log, and the
    # search after publish() reads the v4 bundle in the graph folder.
    store = GraphStore.open(
        root,
        data_dir=tmp_path / "data",
        publish=PublishPolicy(max_bytes=0, max_interval=0, idle=0),
    )
    ada = _axis(768, 0)
    grace = _axis(768, 1)
    fact = _axis(64, 3)

    with _serve(store) as url:
        saved = post_cypher(
            url,
            _SAVE_NODE,
            {"entity_data": _entity(ADA, "Ada", ada)},
            max_records=0,
        )
        assert saved == [{"uuid": ADA}]
        post_cypher(
            url,
            _SAVE_NODE,
            {"entity_data": _entity(GRACE, "Grace", grace)},
            max_records=0,
        )
        edged = post_cypher(
            url,
            _SAVE_EDGE,
            {
                "source": ADA,
                "target": GRACE,
                "uuid": EDGE,
                "edge_data": {
                    "uuid": EDGE,
                    "name": "KNOWS",
                    "fact": "Ada knows Grace",
                    "group_id": "default",
                    "fact_embedding": fact,
                },
            },
            max_records=0,
        )
        assert edged == [{"uuid": EDGE}]

        read = post_cypher(url, _READ_NODE, {"uuid": ADA}, max_records=0)
        assert read[0]["name"] == "Ada"
        assert len(read[0]["name_embedding"]) == 768
        assert read[0]["name_embedding"][0] == 1.0
        assert read[0]["name_embedding"][1] == 0.0

        fact_row = post_cypher(url, _READ_EDGE, {"uuid": EDGE}, max_records=0)
        assert len(fact_row[0]["fact_embedding"]) == 64
        assert fact_row[0]["fact_embedding"][3] == 1.0

        hits = post_cypher(
            url,
            _SEARCH_NODES,
            {
                "group_ids": ["default"],
                "search_vector": ada,
                "min_score": 0.6,
                "limit": 10,
            },
            max_records=0,
        )
        assert [row["uuid"] for row in hits] == [ADA]
        assert hits[0]["name"] == "Ada"
        assert len(hits[0]["attributes"]["name_embedding"]) == 768

        edge_hits = post_cypher(
            url,
            _SEARCH_EDGES,
            {
                "group_ids": ["default"],
                "search_vector": fact,
                "min_score": 0.6,
                "limit": 10,
            },
            max_records=0,
        )
        assert [row["uuid"] for row in edge_hits] == [EDGE]
        assert edge_hits[0]["score"] > 0.6

        text = post_cypher(
            url,
            """
            CALL db.index.fulltext.queryNodes("node_name_and_summary", $query, {limit: 5})
            YIELD node AS n, score
            RETURN n.name AS name
            ORDER BY score DESC
            """,
            {"query": "Grace"},
            max_records=0,
        )
        assert text[0]["name"] == "Grace"

        # Same statement that writes the vector must be able to read it back.
        # The node view is bound before setNodeVectorProperty runs.
        updated = _axis(768, 4)
        same = post_cypher(
            url,
            """
            MATCH (n:Entity {uuid: $uuid})
            CALL db.create.setNodeVectorProperty(n, "name_embedding", $vec)
            RETURN n.name_embedding AS name_embedding
            """,
            {"uuid": GRACE, "vec": updated},
            max_records=0,
        )
        assert len(same[0]["name_embedding"]) == 768
        assert same[0]["name_embedding"][4] == 1.0

        # Seal the active log into v4 bundles, then search again through serve.
        assert store.publish() >= 1
        assert list((root / "vectors" / "name_embedding").glob("w*.jsonl"))
        assert list((root / "vectors" / "fact_embedding").glob("w*.jsonl"))
        sealed = post_cypher(
            url,
            _SEARCH_NODES,
            {
                "group_ids": ["default"],
                "search_vector": ada,
                "min_score": 0.6,
                "limit": 10,
            },
            max_records=0,
        )
        assert [row["uuid"] for row in sealed] == [ADA]

    name_files = list((root / "vectors" / "name_embedding").glob("*.jsonl"))
    fact_files = list((root / "vectors" / "fact_embedding").glob("*.jsonl"))
    assert name_files
    assert fact_files
    reopened = GraphStore.open(root, data_dir=tmp_path / "data")
    stored = reopened.get_vector("name_embedding", ADA)
    assert stored is not None
    assert stored.dim == 768
    assert stored.floats()[0] == 1.0
    fact_stored = reopened.get_vector("fact_embedding", EDGE)
    assert fact_stored is not None
    assert fact_stored.dim == 64
    assert fact_stored.floats()[3] == 1.0
