import threading
from contextlib import contextmanager
from pathlib import Path

from graph_ted_db.driver.http import (
    get_health,
    post_cypher,
    post_cypher_many,
)
from graph_ted_db.server import make_server
from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="client")
    g = GraphStore.open(root, data_dir=tmp_path / "data")
    g.make_node(labels=["Entity"], props={"name": "Alice", "group_id": "default"})
    return g


@contextmanager
def _serve(store: GraphStore):
    server = make_server(store, "127.0.0.1", 0, max_records=500)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{int(port)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_client_health_and_query(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as url:
        health = get_health(url)
        assert health["ok"] is True
        assert health["name"] == "client"
        rows = post_cypher(
            url,
            "MATCH (n:Entity {group_id: $gid}) RETURN n.name AS name",
            {"gid": "default"},
        )
        assert rows == [{"name": "Alice"}]
        post_cypher(
            url,
            "CREATE (n:Entity {name: $name, group_id: $gid}) RETURN n.name AS name",
            {"name": "Bob", "gid": "default"},
            updated_by="alice",
        )
    bob = next(n for n in g.iter_nodes() if n.props.get("name") == "Bob")
    assert bob.updated_by == "alice"


def test_http_client_batch_and_rollback(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as url:
        results = post_cypher_many(
            url,
            [
                (
                    "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name "
                    "SET n.group_id = $gid RETURN n.name AS name",
                    {
                        "uuid": "00000000-0000-4000-8000-0000000000d1",
                        "name": "Dana",
                        "gid": "default",
                    },
                ),
                ("MATCH (n:Entity) RETURN n.name AS name", {}),
            ],
        )
        assert results[0] == [{"name": "Dana"}]
        names = {row["name"] for row in results[1]}
        assert names == {"Alice", "Dana"}
        try:
            post_cypher_many(
                url,
                [
                    (
                        "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name "
                        "SET n.group_id = $gid RETURN n.name AS name",
                        {
                            "uuid": "00000000-0000-4000-8000-0000000000d2",
                            "name": "Temp",
                            "gid": "default",
                        },
                    ),
                    ("MATCH (n) RETURN bogus(n) AS x", {}),
                ],
            )
        except ValueError as exc:
            assert "400" in str(exc)
        else:
            raise AssertionError("expected batch failure")
    leftover = g.execute(
        "MATCH (n:Entity {name: $name}) RETURN n.name AS name",
        {"name": "Temp"},
    )
    assert leftover == []
