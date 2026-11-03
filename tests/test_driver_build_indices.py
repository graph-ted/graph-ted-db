"""GraphTedDbDriver.build_indices_and_constraints: required by graphiti-core 0.31+, a no-op here."""

import asyncio
import importlib
import importlib.metadata
import inspect

import pytest

from graph_ted_db.driver import http
from graph_ted_db.store import init_graph
from tests.test_async_driver import _driver_cls


def test_build_indices_is_async_noop_on_folder(tmp_path):
    root = tmp_path / "g"
    init_graph(root, name="g")
    driver = _driver_cls()(root)
    assert inspect.iscoroutinefunction(driver.build_indices_and_constraints)
    assert asyncio.run(driver.build_indices_and_constraints()) is None
    assert asyncio.run(driver.build_indices_and_constraints(delete_existing=True)) is None
    rows, _, _ = asyncio.run(driver.execute_query("RETURN 1 AS ok"))
    assert rows[0]["ok"] == 1


def test_build_indices_sends_nothing_over_http(monkeypatch):
    cls = _driver_cls()
    mod = importlib.import_module(cls.__module__)
    calls = []
    for name in ("post_cypher", "post_cypher_many", "get_health"):
        monkeypatch.setattr(mod, name, lambda *a, _n=name, **k: calls.append(_n))
        monkeypatch.setattr(http, name, lambda *a, _n=name, **k: calls.append(_n))
    driver = cls("http://127.0.0.1:9")
    asyncio.run(driver.build_indices_and_constraints(delete_existing=True))
    assert calls == []


def test_delete_existing_calls_delete_all_indexes(tmp_path):
    root = tmp_path / "g"
    init_graph(root, name="g")
    driver = _driver_cls()(root)
    seen = []

    async def _fake():
        seen.append(True)

    driver.delete_all_indexes = _fake
    asyncio.run(driver.build_indices_and_constraints())
    assert seen == []
    asyncio.run(driver.build_indices_and_constraints(delete_existing=True))
    assert seen == [True]


def test_constructs_on_installed_graphiti_core(tmp_path):
    """With real graphiti-core installed (0.31+ made the method abstract), the driver constructs."""
    try:
        importlib.metadata.version("graphiti-core")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("graphiti-core not installed")
    from graph_ted_db.driver.graphiti import GraphTedDbDriver

    assert not getattr(GraphTedDbDriver, "__abstractmethods__", frozenset())
    root = tmp_path / "g"
    init_graph(root, name="g")
    GraphTedDbDriver(root)
