import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

import graph_ted_db
from graph_ted_db.server import make_server, serve
from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="http")
    g = GraphStore.open(root, data_dir=tmp_path / "data")
    g.make_node(labels=["Entity"], props={"name": "Alice", "group_id": "default"})
    return g


@contextmanager
def _serve(store: GraphStore, *, token: str = "", max_records: int = 2):
    server = make_server(store, "127.0.0.1", 0, max_records=max_records, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _conn(host: str, port: int) -> HTTPConnection:
    return HTTPConnection(host, port, timeout=5)


def test_health_and_info(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        conn.request("GET", "/health")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        # /health is open even when a token is set: status and version only.
        assert body == {"ok": True, "version": graph_ted_db.__version__}
        conn.request("GET", "/info")
        info = json.loads(conn.getresponse().read())
        assert info["nodes"] == 1
        assert info["name"] == "http"
        assert set(info["problems"].values()) == {0}
        conn.close()


def test_post_cypher(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        payload = json.dumps(
            {
                "query": "MATCH (n:Entity {group_id: $gid}) RETURN n.name AS name",
                "parameters": {"gid": "default"},
            }
        ).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert body["truncated"] is False
        assert body["records"] == [{"name": "Alice"}]
        conn.close()


def test_get_cypher_and_return_1_as_ok(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        conn.request("GET", "/cypher?query=RETURN%201%20AS%20ok")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert body["records"] == [{"ok": 1}]
        conn.close()


def test_cypher_error_is_400(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        payload = json.dumps({"query": "MATCH (n) RETURN bogus(n) AS x"}).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 400
        assert body["type"] == "cypher"
        conn.close()


def test_truncation(tmp_path: Path):
    g = _open(tmp_path)
    g.make_node(labels=["Entity"], props={"name": "Bob", "group_id": "default"})
    g.make_node(labels=["Entity"], props={"name": "Cara", "group_id": "default"})
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        payload = json.dumps({"query": "MATCH (n:Entity) RETURN n.name AS name"}).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert body["truncated"] is True
        assert len(body["records"]) == 2
        conn.close()


def test_http_cypher_sees_put_from_another_process(tmp_path: Path):
    g = _open(tmp_path)
    writer = GraphStore.open(g.root, data_dir=tmp_path / "data")
    with _serve(g) as (host, port):
        writer.make_node(labels=["Entity"], props={"name": "Bob", "group_id": "default"})
        conn = _conn(host, port)
        conn.request("GET", "/cypher?query=MATCH%20(n)%20RETURN%20n.name%20AS%20name")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        names = {row["name"] for row in body["records"]}
        assert names == {"Alice", "Bob"}
        conn.close()


def test_second_listener_on_same_port_fails(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        with pytest.raises(OSError):
            other = make_server(g, host, port)
            other.server_close()


def test_post_cypher_statements_batch(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        payload = json.dumps(
            {
                "statements": [
                    {
                        "query": (
                            "MERGE (n:Entity {uuid: $uuid}) "
                            "SET n.name = $name SET n.group_id = $gid "
                            "RETURN n.name AS name"
                        ),
                        "parameters": {
                            "uuid": "00000000-0000-4000-8000-0000000000c1",
                            "name": "Cara",
                            "gid": "default",
                        },
                    },
                    {
                        "query": "MATCH (n:Entity) RETURN n.name AS name",
                    },
                ]
            }
        ).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert [row["name"] for row in body["results"][0]["records"]] == ["Cara"]
        names = {row["name"] for row in body["results"][1]["records"]}
        assert "Alice" in names
        assert "Cara" in names
        conn.close()


def test_post_cypher_statements_rollback(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        payload = json.dumps(
            {
                "statements": [
                    {
                        "query": (
                            "MERGE (n:Entity {uuid: $uuid}) "
                            "SET n.name = $name SET n.group_id = $gid "
                            "RETURN n.name AS name"
                        ),
                        "parameters": {
                            "uuid": "00000000-0000-4000-8000-0000000000c2",
                            "name": "Temp",
                            "gid": "default",
                        },
                    },
                    {"query": "MATCH (n) RETURN bogus(n) AS x"},
                ]
            }
        ).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 400
        assert body["type"] == "cypher"
        conn.close()
    leftover = g.execute(
        "MATCH (n:Entity {name: $name}) RETURN n.name AS name",
        {"name": "Temp"},
    )
    assert leftover == []


def test_unknown_path_404(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        conn.request("GET", "/nope")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 404
        assert body["type"] == "request"
        conn.close()


def test_get_cypher_rejects_writes(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        conn.request("GET", "/cypher?query=MATCH%20(n)%20DETACH%20DELETE%20n")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 405
        assert body["type"] == "request"
        conn.request("GET", "/cypher?query=MATCH%20(n)%20RETURN%20n.name%20AS%20name")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert {row["name"] for row in body["records"]} == {"Alice"}
        conn.close()
    leftover = g.execute("MATCH (n) RETURN n.name AS name")
    assert leftover == [{"name": "Alice"}]


def test_post_cypher_updated_by(tmp_path: Path):
    g = _open(tmp_path)
    with _serve(g) as (host, port):
        conn = _conn(host, port)
        payload = json.dumps(
            {
                "query": ("CREATE (n:Entity {name: $name, group_id: $gid}) RETURN n.uuid AS uuid"),
                "parameters": {"name": "Bob", "gid": "default"},
                "updated_by": "alice",
            }
        ).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        uuid = body["records"][0]["uuid"]
        conn.close()
    rec = g.get_node(uuid)
    assert rec is not None
    assert rec.updated_by == "alice"


def test_token_required_except_health(tmp_path: Path):
    g = _open(tmp_path)
    token = "s3cret"
    with _serve(g, token=token) as (host, port):
        conn = _conn(host, port)
        conn.request("GET", "/health")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert body["ok"] is True

        conn.request("GET", "/info")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 401
        assert body["type"] == "auth"

        conn.request("GET", "/cypher?query=RETURN%201%20AS%20ok")
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 401

        conn.request(
            "GET",
            "/cypher?query=RETURN%201%20AS%20ok",
            headers={"Authorization": f"Bearer {token}"},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert body["records"] == [{"ok": 1}]

        payload = json.dumps({"query": "RETURN 1 AS ok"}).encode()
        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={
                "Content-Length": str(len(payload)),
                "X-Graph-Ted-Token": token,
            },
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 200
        assert body["records"] == [{"ok": 1}]

        conn.request(
            "POST",
            "/cypher",
            body=payload,
            headers={
                "Content-Length": str(len(payload)),
                "Authorization": "Bearer wrong",
            },
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        assert res.status == 401
        conn.close()


def test_serve_refuses_off_loopback_without_token(tmp_path: Path):
    g = _open(tmp_path)
    with pytest.raises(ValueError, match="token"):
        serve(g, host="0.0.0.0", port=0)
