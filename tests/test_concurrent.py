"""Concurrent writers share one GraphStore. The HTTP server is threaded."""

import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

from graph_ted_db.server import make_server
from graph_ted_db.store import GraphStore, init_graph

WORKERS = 20
WRITE = """
MATCH (a {uuid: $anchor})
CREATE (n:Entity {uuid: $uuid, name: $name, group_id: 'batch'})
CREATE (a)-[r:RELATES_TO {uuid: $eid, group_id: 'batch'}]->(n)
RETURN n.uuid AS uuid, r.uuid AS eid
"""
READ = "MATCH (n:Entity {group_id: 'batch'}) RETURN n.name AS name"


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _open(tmp_path: Path) -> tuple[Path, GraphStore]:
    root = tmp_path / "g"
    init_graph(root, name="concurrent")
    store = GraphStore.open(root, data_dir=tmp_path / "data")
    return root, store


def test_lock_blocks_other_threads(tmp_path: Path):
    _root, store = _open(tmp_path)
    events: list[str] = []

    def hold() -> None:
        with store._lock():
            events.append("hold-in")
            time.sleep(0.2)
            events.append("hold-out")

    def other() -> None:
        time.sleep(0.05)
        with store._lock():
            events.append("other-in")
            events.append("other-out")

    threads = [threading.Thread(target=hold), threading.Thread(target=other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert events == ["hold-in", "hold-out", "other-in", "other-out"]


def test_inprocess_concurrent_execute(tmp_path: Path):
    root, store = _open(tmp_path)
    anchor = _uuid(1)
    store.execute(
        "CREATE (a:Entity {uuid: $uuid, name: 'anchor', group_id: 'batch'}) RETURN a.uuid AS uuid",
        {"uuid": anchor},
    )
    errors: list[BaseException] = []

    def write(i: int) -> None:
        try:
            rows = store.execute(
                WRITE,
                {
                    "anchor": anchor,
                    "uuid": _uuid(100 + i),
                    "eid": _uuid(200 + i),
                    "name": f"n{i}",
                },
            )
            if rows != [{"uuid": _uuid(100 + i), "eid": _uuid(200 + i)}]:
                errors.append(AssertionError(rows))
        except BaseException as exc:
            errors.append(exc)

    def read(_i: int) -> None:
        try:
            store.execute(READ)
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = []
        for i in range(WORKERS):
            futures.append(pool.submit(write, i))
            futures.append(pool.submit(read, i))
        for future in futures:
            future.result()
    assert errors == []
    store.close() if hasattr(store, "close") else None
    reopened = GraphStore.open(root, data_dir=tmp_path / "data")
    names = {node.props.get("name") for node in reopened.iter_nodes()}
    assert names == {"anchor"} | {f"n{i}" for i in range(WORKERS)}
    assert len(list(reopened.iter_edges())) == WORKERS


def test_http_concurrent_writes_and_reads(tmp_path: Path):
    root, store = _open(tmp_path)
    anchor = _uuid(1)
    store.execute(
        "CREATE (a:Entity {uuid: $uuid, name: 'anchor', group_id: 'batch'}) RETURN a.uuid AS uuid",
        {"uuid": anchor},
    )
    server = make_server(store, "127.0.0.1", 0, max_records=1000)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    errors: list[BaseException] = []

    def post(query: str, params: dict) -> dict:
        body = json.dumps({"query": query, "parameters": params}).encode()
        conn = HTTPConnection(host, int(port), timeout=30)
        try:
            conn.request(
                "POST",
                "/cypher",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            payload = json.loads(response.read().decode())
            if response.status != 200:
                raise AssertionError((response.status, payload))
            return payload
        finally:
            conn.close()

    def write(i: int) -> None:
        try:
            payload = post(
                WRITE,
                {
                    "anchor": anchor,
                    "uuid": _uuid(100 + i),
                    "eid": _uuid(200 + i),
                    "name": f"n{i}",
                },
            )
            records = payload.get("records")
            if records != [{"uuid": _uuid(100 + i), "eid": _uuid(200 + i)}]:
                errors.append(AssertionError(records))
        except BaseException as exc:
            errors.append(exc)

    def read(_i: int) -> None:
        try:
            payload = post(READ, {})
            if not isinstance(payload.get("records"), list):
                errors.append(AssertionError(payload))
        except BaseException as exc:
            errors.append(exc)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = []
            for i in range(WORKERS):
                futures.append(pool.submit(write, i))
                futures.append(pool.submit(read, i))
            for future in futures:
                future.result()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert errors == []
    reopened = GraphStore.open(root, data_dir=tmp_path / "data")
    names = {node.props.get("name") for node in reopened.iter_nodes()}
    assert names == {"anchor"} | {f"n{i}" for i in range(WORKERS)}
    assert len(list(reopened.iter_edges())) == WORKERS


def test_http_listen_backlog_absorbs_connection_burst(tmp_path: Path):
    # The accept loop is not running, so every connection has to wait in the
    # kernel's listen queue. With socketserver's default backlog of 5 the
    # extra connects stall or get reset; the server must queue a burst.
    _root, store = _open(tmp_path)
    server = make_server(store, "127.0.0.1", 0)
    host, port = server.server_address[:2]
    assert server.request_queue_size >= 128
    clients: list[socket.socket] = []
    try:
        for _ in range(32):
            clients.append(socket.create_connection((host, port), timeout=0.5))
    finally:
        for client in clients:
            client.close()
        server.server_close()
    assert len(clients) == 32
