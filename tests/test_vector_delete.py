"""Deleting a node or edge drops its embeddings from vector search.

Embeddings live in ``vectors/``, separate from the node record. A delete has
to tombstone them in the same transaction, and search has to ignore an
embedding whose owner is gone, including when another writer stored a newer
vector.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from graph_ted_db import CypherError, GraphStore, init_graph
from graph_ted_db.driver.http import post_cypher
from graph_ted_db.server import make_server
from graph_ted_db.store import PublishPolicy
from graph_ted_db.store.records import VectorRecord

ADA = "00000000-0000-4000-8000-0000000000a1"
ADA_B = "00000000-0000-4000-8000-0000000000a2"
GRACE = "00000000-0000-4000-8000-0000000000b1"
GRACE_B = "00000000-0000-4000-8000-0000000000b2"
LIN = "00000000-0000-4000-8000-0000000000c1"
ADA_EDGE = "00000000-0000-4000-8000-0000000000e1"
GRACE_EDGE = "00000000-0000-4000-8000-0000000000e2"

_QUIET = PublishPolicy(max_bytes=0, max_interval=0, idle=0, compact_after=0)

_SEARCH_NODES = """
MATCH (n:Entity)
WHERE n.group_id IN $group_ids
WITH n, vector.similarity.cosine(n.name_embedding, $search_vector) AS score
WHERE score > $min_score
RETURN n.uuid AS uuid
ORDER BY score DESC
LIMIT $limit
"""

_SEARCH_EDGES = """
MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
WHERE e.group_id IN $group_ids
WITH e, vector.similarity.cosine(e.fact_embedding, $search_vector) AS score
WHERE score > $min_score
RETURN e.uuid AS uuid
ORDER BY score DESC
LIMIT $limit
"""

_CLEAR_EDGE = "MATCH ()-[r {group_id: $group_id}]->() DELETE r"
_CLEAR_NODE = "MATCH (n {group_id: $group_id}) DETACH DELETE n"


def _axis(index: int) -> list[float]:
    values = [0.0] * 4
    values[index] = 1.0
    return values


def _open(tmp_path: Path, name: str = "g") -> GraphStore:
    root = tmp_path / name
    init_graph(root, name="vectors")
    return GraphStore.open(root, data_dir=tmp_path / f"data-{name}", publish=_QUIET)


def _put_node(g: GraphStore, record_id: str, name: str, group: str) -> None:
    g.make_node(
        record_id=record_id,
        labels=["Entity"],
        props={"uuid": record_id, "name": name, "group_id": group},
    )


def _put_embedding(g: GraphStore, record_id: str, prop: str, values: list[float]) -> None:
    g.put_vector(
        VectorRecord.from_floats(
            id=record_id,
            property=prop,
            values=values,
            updated_at=g._clock_now(),
        )
    )


def _seed_two_groups(g: GraphStore) -> dict[str, list[float]]:
    """Alpha (Ada) and beta (Grace), each with a name vector and a fact vector."""
    vectors = {
        "ada": _axis(0),
        "grace": _axis(1),
        "ada_fact": _axis(2),
        "grace_fact": _axis(3),
    }
    _put_node(g, ADA, "Ada", "alpha")
    _put_node(g, ADA_B, "Ada-b", "alpha")
    _put_node(g, GRACE, "Grace", "beta")
    _put_node(g, GRACE_B, "Grace-b", "beta")
    g.make_edge(
        record_id=ADA_EDGE,
        type="RELATES_TO",
        from_id=ADA,
        to_id=ADA_B,
        props={"uuid": ADA_EDGE, "group_id": "alpha", "fact": "Ada link"},
    )
    g.make_edge(
        record_id=GRACE_EDGE,
        type="RELATES_TO",
        from_id=GRACE,
        to_id=GRACE_B,
        props={"uuid": GRACE_EDGE, "group_id": "beta", "fact": "Grace link"},
    )
    _put_embedding(g, ADA, "name_embedding", vectors["ada"])
    _put_embedding(g, GRACE, "name_embedding", vectors["grace"])
    _put_embedding(g, ADA_EDGE, "fact_embedding", vectors["ada_fact"])
    _put_embedding(g, GRACE_EDGE, "fact_embedding", vectors["grace_fact"])
    return vectors


def _search(g: GraphStore, query: str, vector: list[float], groups: list[str]) -> list[str]:
    rows = g.execute(
        query,
        {
            "group_ids": groups,
            "search_vector": vector,
            "min_score": 0.6,
            "limit": 10,
        },
    )
    return [row["uuid"] for row in rows]


def _assert_only_grace(g: GraphStore, vectors: dict[str, list[float]]) -> None:
    assert _search(g, _SEARCH_NODES, vectors["ada"], ["alpha", "beta"]) == []
    assert _search(g, _SEARCH_NODES, vectors["grace"], ["alpha", "beta"]) == [GRACE]
    assert _search(g, _SEARCH_EDGES, vectors["ada_fact"], ["alpha", "beta"]) == []
    assert _search(g, _SEARCH_EDGES, vectors["grace_fact"], ["alpha", "beta"]) == [GRACE_EDGE]
    assert g.get_vector("name_embedding", ADA) is None
    assert g.get_vector("fact_embedding", ADA_EDGE) is None
    grace = g.get_vector("name_embedding", GRACE)
    assert grace is not None
    assert grace.floats()[1] == 1.0
    fact = g.get_vector("fact_embedding", GRACE_EDGE)
    assert fact is not None
    assert fact.floats()[3] == 1.0


def test_delete_node_drops_its_vectors_and_keeps_the_other_group(tmp_path: Path) -> None:
    g = _open(tmp_path)
    vectors = _seed_two_groups(g)
    assert _search(g, _SEARCH_NODES, vectors["ada"], ["alpha", "beta"]) == [ADA]

    g.delete_node(ADA)
    _assert_only_grace(g, vectors)

    # Same id comes back without a new embedding: the deleted vector stays dead.
    _put_node(g, ADA, "Ada", "alpha")
    assert g.get_node(ADA) is not None
    assert g.get_vector("name_embedding", ADA) is None
    assert _search(g, _SEARCH_NODES, vectors["ada"], ["alpha", "beta"]) == []
    assert _search(g, _SEARCH_NODES, vectors["grace"], ["alpha", "beta"]) == [GRACE]

    revived = _axis(0)
    revived[0] = 0.0
    revived[2] = 1.0
    _put_embedding(g, ADA, "name_embedding", revived)
    assert _search(g, _SEARCH_NODES, revived, ["alpha", "beta"]) == [ADA]
    assert _search(g, _SEARCH_NODES, vectors["grace"], ["alpha", "beta"]) == [GRACE]


def test_cypher_group_clear_drops_only_that_group(tmp_path: Path) -> None:
    g = _open(tmp_path)
    vectors = _seed_two_groups(g)
    g.execute(_CLEAR_EDGE, {"group_id": "alpha"})
    g.execute(_CLEAR_NODE, {"group_id": "alpha"})
    _assert_only_grace(g, vectors)
    assert g.get_node(ADA) is None
    assert g.get_node(ADA_B) is None
    assert g.get_node(GRACE) is not None

    # Seal, then open as another process. It only sees the graph folder.
    g.publish()
    reopened = GraphStore.open(g.root, data_dir=tmp_path / "reopen", publish=_QUIET)
    _assert_only_grace(reopened, vectors)


def test_write_and_delete_in_one_transaction_leaves_no_vector(tmp_path: Path) -> None:
    g = _open(tmp_path)
    ada = _axis(0)
    g.execute(
        """
        MERGE (n:Entity {uuid: $uuid})
        SET n = $entity
        WITH n
        CALL db.create.setNodeVectorProperty(n, "name_embedding", $entity.name_embedding)
        DETACH DELETE n
        """,
        {
            "uuid": ADA,
            "entity": {
                "uuid": ADA,
                "name": "Ada",
                "group_id": "alpha",
                "labels": ["Entity"],
                "name_embedding": ada,
            },
        },
    )
    assert g.get_node(ADA) is None
    assert g.get_vector("name_embedding", ADA) is None
    assert _search(g, _SEARCH_NODES, ada, ["alpha"]) == []
    _put_node(g, ADA, "Ada", "alpha")
    assert g.get_vector("name_embedding", ADA) is None
    assert _search(g, _SEARCH_NODES, ada, ["alpha"]) == []


def test_failed_batch_does_not_drop_vectors(tmp_path: Path) -> None:
    g = _open(tmp_path)
    vectors = _seed_two_groups(g)
    with pytest.raises(CypherError):
        g.execute_many(
            [
                (_CLEAR_NODE, {"group_id": "alpha"}),
                ("THIS WILL NOT PARSE", {}),
            ]
        )
    assert _search(g, _SEARCH_NODES, vectors["ada"], ["alpha", "beta"]) == [ADA]
    assert g.get_vector("name_embedding", ADA) is not None
    assert _search(g, _SEARCH_NODES, vectors["grace"], ["alpha", "beta"]) == [GRACE]


def test_compact_omits_tombstoned_vectors(tmp_path: Path) -> None:
    g = _open(tmp_path)
    _seed_two_groups(g)
    g.delete_node(ADA)
    g.execute(_CLEAR_NODE, {"group_id": "alpha"})
    g.compact()
    names = _vector_text(g.root / "vectors" / "name_embedding")
    facts = _vector_text(g.root / "vectors" / "fact_embedding")
    assert ADA not in names
    assert GRACE in names
    assert ADA_EDGE not in facts
    assert GRACE_EDGE in facts


def test_http_delete_and_inprocess_delete_hide_vectors(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root, name="vectors")
    store = GraphStore.open(root, data_dir=tmp_path / "data", publish=_QUIET)
    server = make_server(store, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{int(port)}"
    ada, grace, lin = _axis(0), _axis(1), _axis(2)
    try:
        for record_id, name, group, embedding in (
            (ADA, "Ada", "alpha", ada),
            (GRACE, "Grace", "beta", grace),
            (LIN, "Lin", "gamma", lin),
        ):
            saved = post_cypher(
                url,
                """
                MERGE (n:Entity {uuid: $uuid})
                SET n:Entity
                SET n = $entity
                WITH n
                CALL db.create.setNodeVectorProperty(n, "name_embedding", $entity.name_embedding)
                RETURN n.uuid AS uuid
                """,
                {
                    "uuid": record_id,
                    "entity": {
                        "uuid": record_id,
                        "name": name,
                        "group_id": group,
                        "labels": ["Entity"],
                        "name_embedding": embedding,
                    },
                },
                max_records=0,
            )
            assert saved == [{"uuid": record_id}]

        post_cypher(url, _CLEAR_EDGE, {"group_id": "alpha"}, max_records=0)
        post_cypher(url, _CLEAR_NODE, {"group_id": "alpha"}, max_records=0)

        groups = ["alpha", "beta", "gamma"]
        assert _http_search(url, ada, groups) == []
        assert _http_search(url, grace, groups) == [GRACE]
        assert _http_search(url, lin, groups) == [LIN]
        assert store.get_vector("name_embedding", ADA) is None
        assert store.get_vector("name_embedding", GRACE) is not None

        post_cypher(
            url,
            """
            MERGE (n:Entity {uuid: $uuid})
            SET n.group_id = "alpha"
            SET n.name = "Ada"
            RETURN n.uuid AS uuid
            """,
            {"uuid": ADA},
            max_records=0,
        )
        assert _http_search(url, ada, groups) == []
        assert _http_search(url, lin, groups) == [LIN]

        store.delete_node(GRACE)
        assert _http_search(url, grace, groups) == []
        assert _http_search(url, lin, groups) == [LIN]
        assert store.get_vector("name_embedding", GRACE) is None
        assert store.get_vector("name_embedding", LIN) is not None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_newer_vector_from_other_writer_stays_unsearchable(tmp_path: Path) -> None:
    """Two devices, OneDrive-style copy. B's newer embedding must not resurrect search."""
    a_root = tmp_path / "a" / "g"
    b_root = tmp_path / "b" / "g"
    init_graph(a_root, name="vectors")
    shutil.copytree(a_root, b_root)
    sa = GraphStore.open(a_root, data_dir=tmp_path / "da", publish=_QUIET)
    ada, grace = _axis(0), _axis(1)
    _put_node(sa, ADA, "Ada", "alpha")
    _put_node(sa, GRACE, "Grace", "beta")
    _put_embedding(sa, ADA, "name_embedding", ada)
    _put_embedding(sa, GRACE, "name_embedding", grace)
    sa.publish()
    _sync(a_root, b_root)
    sb = GraphStore.open(b_root, data_dir=tmp_path / "db", publish=_QUIET)
    assert sb.get_node(ADA) is not None

    sa.delete_node(ADA)
    sa.publish()
    sb.put_vector(
        VectorRecord.from_floats(
            id=ADA,
            property="name_embedding",
            values=ada,
            updated_at="2099-01-01T00:00:00.000000Z",
        )
    )
    sb.publish()
    sa.close()
    sb.close()
    _sync(a_root, b_root)

    for root, dev in ((a_root, "a2"), (b_root, "b2")):
        opened = GraphStore.open(root, data_dir=tmp_path / dev, publish=_QUIET)
        assert opened.get_node(ADA) is None
        assert opened.get_vector("name_embedding", ADA) is None
        assert _search(opened, _SEARCH_NODES, ada, ["alpha", "beta"]) == []
        assert _search(opened, _SEARCH_NODES, grace, ["alpha", "beta"]) == [GRACE]
        grace_vec = opened.get_vector("name_embedding", GRACE)
        assert grace_vec is not None and grace_vec.floats()[1] == 1.0
    # B's vector line is really on disk; search hid it because the node is gone.
    blob = _vector_text(a_root / "vectors" / "name_embedding")
    assert "2099-01-01T00:00:00.000000Z" in blob


def _vector_text(directory: Path) -> str:
    parts = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*.jsonl")):
            parts.append(path.read_text(encoding="utf-8"))
    return "".join(parts)


def _sync(*roots: Path) -> None:
    names: set[Path] = set()
    for root in roots:
        names |= {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    for rel in names:
        present = [root / rel for root in roots if (root / rel).is_file()]
        newest = max(present, key=lambda path: (path.stat().st_mtime_ns, path.stat().st_size))
        for root in roots:
            dest = root / rel
            if dest != newest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(newest, dest)


def _http_search(url: str, vector: list[float], groups: list[str]) -> list[str]:
    rows = post_cypher(
        url,
        _SEARCH_NODES,
        {
            "group_ids": groups,
            "search_vector": vector,
            "min_score": 0.6,
            "limit": 10,
        },
        max_records=0,
    )
    return [row["uuid"] for row in rows]


_CRASH_WORKER = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from graph_ted_db import GraphStore, init_graph
    from graph_ted_db.store import PublishPolicy
    from graph_ted_db.store.records import VectorRecord

    root, data = Path(sys.argv[1]), Path(sys.argv[2])
    ada = "00000000-0000-4000-8000-0000000000a1"
    init_graph(root, name="crash", exist_ok=True)
    g = GraphStore.open(
        root,
        data_dir=data,
        publish=PublishPolicy(max_bytes=0, max_interval=0, idle=0, compact_after=0),
    )
    g.make_node(
        record_id=ada,
        labels=["Entity"],
        props={"uuid": ada, "name": "Ada", "group_id": "alpha"},
    )
    g.put_vector(
        VectorRecord.from_floats(
            id=ada,
            property="name_embedding",
            values=[1.0, 0.0, 0.0, 0.0],
            updated_at=g._clock_now(),
        )
    )
    g.delete_node(ada)
    print("acked", flush=True)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="crash harness needs SIGKILL")
@pytest.mark.parametrize("crash_at", ["before-wal", "after-wal", "mid-apply"])
def test_delete_node_and_vectors_commit_together(tmp_path: Path, crash_at: str) -> None:
    root = tmp_path / "g"
    data = tmp_path / "data"
    script = tmp_path / "worker.py"
    script.write_text(_CRASH_WORKER, encoding="utf-8")
    env = dict(os.environ)
    env["GRAPH_TED_DB_CRASH_AT"] = crash_at
    proc = subprocess.run(
        [sys.executable, str(script), str(root), str(data)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode < 0, proc.stderr
    assert "acked" not in proc.stdout
    meta = json.loads((root / "graph.json").read_text(encoding="utf-8"))
    wal = data / meta["id"] / "wal" / "current.jsonl"
    if crash_at == "before-wal":
        assert not wal.is_file() or wal.stat().st_size == 0
    else:
        text = wal.read_text(encoding="utf-8")
        assert "delete_node" in text
        assert "delete_vector" in text
        assert "name_embedding" in text

    opened = GraphStore.open(root, data_dir=data, publish=_QUIET)
    ada = "00000000-0000-4000-8000-0000000000a1"
    if crash_at == "before-wal":
        assert opened.get_node(ada) is not None
        stored = opened.get_vector("name_embedding", ada)
        assert stored is not None and stored.floats()[0] == 1.0
        assert _search(opened, _SEARCH_NODES, [1.0, 0.0, 0.0, 0.0], ["alpha"]) == [ada]
    else:
        assert opened.get_node(ada) is None
        assert opened.get_vector("name_embedding", ada) is None
        assert _search(opened, _SEARCH_NODES, [1.0, 0.0, 0.0, 0.0], ["alpha"]) == []
        assert not wal.is_file() or wal.stat().st_size == 0
