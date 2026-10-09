"""Async Graphiti driver calls must not block the event loop on HTTP."""

import asyncio
import sys
import time
import types
from pathlib import Path

import graph_ted_db.driver.http as http
from graph_ted_db.store import GraphStore, init_graph


def _driver_cls():
    """Import the driver. graphiti-core is optional; stub the base classes."""
    try:
        from graph_ted_db.driver.graphiti import GraphTedDbDriver
    except ImportError:
        sys.modules.pop("graph_ted_db.driver.graphiti", None)
        provider = types.SimpleNamespace(NEO4J="neo4j")

        class GraphDriver:
            def __init__(self):
                return None

        class GraphDriverSession:
            pass

        mod = types.ModuleType("graphiti_core.driver.driver")
        mod.GraphDriver = GraphDriver
        mod.GraphDriverSession = GraphDriverSession
        mod.GraphProvider = provider
        sys.modules.setdefault("graphiti_core", types.ModuleType("graphiti_core"))
        sys.modules.setdefault("graphiti_core.driver", types.ModuleType("graphiti_core.driver"))
        sys.modules["graphiti_core.driver.driver"] = mod
        from graph_ted_db.driver.graphiti import GraphTedDbDriver
    return GraphTedDbDriver


def test_concurrent_async_queries_overlap(monkeypatch):
    delay = 0.3

    def slow_post(base_url, payload, timeout):
        del base_url, payload, timeout
        time.sleep(delay)
        return {"records": [{"ok": 1}]}

    monkeypatch.setattr(http, "_post", slow_post)
    GraphTedDbDriver = _driver_cls()
    driver = GraphTedDbDriver("http://127.0.0.1:9")
    order: list[str] = []

    async def query(label: str):
        order.append(f"start-{label}")
        rows, _, _ = await driver.execute_query("RETURN 1 AS ok")
        order.append(f"end-{label}")
        return rows

    async def tick():
        await asyncio.sleep(0.05)
        order.append("tick")

    async def go():
        started = time.perf_counter()
        rows = await asyncio.gather(query("a"), query("b"), tick())
        return time.perf_counter() - started, rows

    elapsed, rows = asyncio.run(go())
    assert rows[0] == [{"ok": 1}]
    assert rows[1] == [{"ok": 1}]
    # The loop ran `tick` while both HTTP calls were in worker threads.
    assert order.index("tick") < order.index("end-a")
    assert order.index("tick") < order.index("end-b")
    # Two 0.3s calls back to back would take at least 0.6s.
    assert elapsed < 0.55


def test_inprocess_concurrent_writes(tmp_path: Path):
    """to_thread workers may enter store.execute together. Writes must all land."""
    root = tmp_path / "g"
    init_graph(root, name="inproc")
    data = tmp_path / "data"
    GraphTedDbDriver = _driver_cls()
    driver = GraphTedDbDriver(root, data_dir=data)
    anchor = "00000000-0000-4000-8000-000000000001"
    assert driver.store is not None
    driver.store.execute(
        "CREATE (a:Entity {uuid: $uuid, name: 'anchor'}) RETURN a.uuid AS uuid",
        {"uuid": anchor},
    )
    workers = 20

    async def write(i: int) -> list:
        uid = f"00000000-0000-4000-8000-{100 + i:012d}"
        eid = f"00000000-0000-4000-8000-{200 + i:012d}"
        rows, _, _ = await driver.execute_query(
            """
            MATCH (a {uuid: $anchor})
            CREATE (n:Entity {uuid: $uuid, name: $name})
            CREATE (a)-[r:RELATES_TO {uuid: $eid}]->(n)
            RETURN n.uuid AS uuid
            """,
            anchor=anchor,
            uuid=uid,
            eid=eid,
            name=f"n{i}",
        )
        return rows

    async def read() -> list:
        rows, _, _ = await driver.execute_query("MATCH (n) RETURN n.name AS name")
        return rows

    async def go():
        return await asyncio.gather(
            *[write(i) for i in range(workers)],
            *[read() for _ in range(workers)],
        )

    results = asyncio.run(go())
    assert results[:workers] == [
        [{"uuid": f"00000000-0000-4000-8000-{100 + i:012d}"}] for i in range(workers)
    ]
    reopened = GraphStore.open(root, data_dir=data)
    names = {node.props.get("name") for node in reopened.iter_nodes()}
    assert names == {"anchor"} | {f"n{i}" for i in range(workers)}
    assert len(list(reopened.iter_edges())) == workers


def test_execute_write_reenters_lock_on_holder_thread(tmp_path: Path):
    """The loop thread already holds the store lock. The inner query must not deadlock."""
    root = tmp_path / "g"
    init_graph(root, name="reenter")
    data = tmp_path / "data"
    GraphTedDbDriver = _driver_cls()
    driver = GraphTedDbDriver(root, data_dir=data)
    uid = "00000000-0000-4000-8000-000000000042"

    async def write(session):
        await session.run(
            "CREATE (n:Entity {uuid: $uuid, name: 'inside'}) RETURN n.name AS name",
            uuid=uid,
        )

    async def go():
        session = driver.session()
        await asyncio.wait_for(session.execute_write(write), timeout=5)

    asyncio.run(go())
    reopened = GraphStore.open(root, data_dir=data)
    names = {node.props.get("name") for node in reopened.iter_nodes()}
    assert names == {"inside"}


def test_sync_http_client_unchanged(monkeypatch):
    def slow_post(base_url, payload, timeout):
        del base_url, payload, timeout
        return {"records": [{"name": "Ada"}]}

    monkeypatch.setattr(http, "_post", slow_post)
    rows = http.post_cypher("http://127.0.0.1:9", "RETURN 1")
    assert rows == [{"name": "Ada"}]
