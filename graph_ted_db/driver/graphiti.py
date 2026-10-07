"""graphiti-core GraphDriver over a graph-ted-db folder or HTTP serve.

Reports GraphProvider.NEO4J so Graphiti uses its Neo4j Cypher dialect.
Pass an http(s) URL to use serve (one process owns the folder). Pass a
filesystem path to open GraphStore in-process (library / CLI use).
Install graphiti-core in the process that constructs this driver.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from graph_ted_db.driver.http import (
    get_health,
    is_http_url,
    normalize_base,
    post_cypher,
    post_cypher_many,
)
from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.init import GraphFormatError

try:
    from graphiti_core.driver.driver import GraphDriver, GraphDriverSession, GraphProvider
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "GraphTedDbDriver requires graphiti-core. "
        "Install it in the Graphiti MCP environment."
    ) from exc


def _params_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs = dict(kwargs)
    kwargs.pop("routing_", None)
    kwargs.pop("database_", None)
    nested = kwargs.pop("params", None)
    params: dict[str, Any] = dict(nested) if isinstance(nested, dict) else {}
    params.update(kwargs)
    return params


class GraphTedDbSession(GraphDriverSession):
    provider = GraphProvider.NEO4J

    def __init__(self, driver: GraphTedDbDriver):
        self.driver = driver
        self.store = driver.store
        self._batch: list[tuple[str, dict[str, Any]]] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def close(self):
        return None

    def _enqueue(self, query: str | list, kwargs: dict[str, Any]) -> None:
        assert self._batch is not None
        if isinstance(query, list):
            for cypher, params in query:
                self._batch.append((str(cypher), dict(params or {})))
            return
        self._batch.append((str(query), _params_from_kwargs(kwargs)))

    async def run(self, query: str | list, **kwargs: Any) -> Any:
        if self._batch is not None:
            self._enqueue(query, kwargs)
            return None
        if self.driver._http:
            if isinstance(query, list):
                statements = [
                    (str(cypher), dict(params or {})) for cypher, params in query
                ]
                await asyncio.to_thread(
                    post_cypher_many, self.driver._http, statements
                )
            else:
                await asyncio.to_thread(
                    post_cypher,
                    self.driver._http,
                    str(query),
                    _params_from_kwargs(kwargs),
                )
            return None
        assert self.store is not None
        if isinstance(query, list):
            for cypher, params in query:
                await self._execute_store(cypher, dict(params or {}))
            return None
        await self._execute_store(query, _params_from_kwargs(kwargs))
        return None

    async def _execute_store(self, query: str, params: dict[str, Any]) -> list:
        """Run Cypher off the event loop, unless this thread already holds the store lock.

        ``execute_write`` keeps the file lock on the loop thread and calls
        ``run`` from there. Moving that call to a worker would see the lock
        as re-entrant and corrupt ``_lock_depth``.
        """
        assert self.store is not None
        if self.store._tx is not None or self.store._lock_depth:
            return self.store.execute(str(query), params)
        return await asyncio.to_thread(self.store.execute, str(query), params)

    async def execute_write(self, func, *args, **kwargs):
        if self.driver._http:
            self._batch = []
            try:
                result = await func(self, *args, **kwargs)
                statements = self._batch
                self._batch = None
                if statements:
                    await asyncio.to_thread(
                        post_cypher_many, self.driver._http, statements
                    )
                return result
            except Exception:
                self._batch = None
                raise
        assert self.store is not None
        with self.store._lock():
            self.store._begin_unlocked()
            try:
                result = await func(self, *args, **kwargs)
                self.store._commit_unlocked()
                return result
            except Exception:
                self.store._rollback_unlocked()
                raise


class GraphTedDbDriver(GraphDriver):
    provider = GraphProvider.NEO4J
    aoss_client = None
    fulltext_syntax = ""

    def __init__(self, root: str | Path, *, data_dir: Path | None = None):
        super().__init__()
        raw = str(root).strip()
        if is_http_url(raw):
            self._http = normalize_base(raw)
            self.store = None
        else:
            self._http = None
            path = Path(raw).expanduser().resolve()
            try:
                self.store = GraphStore.open(path, data_dir=data_dir)
            except GraphFormatError:
                init_graph(path, name=path.name, exist_ok=True)
                self.store = GraphStore.open(path, data_dir=data_dir)
        self._database = "neo4j"
        self.client = self

    async def execute_query(self, cypher_query_, **kwargs: Any):
        params = _params_from_kwargs(kwargs)
        if self._http:
            rows = await asyncio.to_thread(
                post_cypher,
                self._http,
                str(cypher_query_),
                params,
                max_records=0,
            )
            return rows, None, None
        assert self.store is not None
        if self.store._tx is not None or self.store._lock_depth:
            rows = self.store.execute(str(cypher_query_), params)
        else:
            rows = await asyncio.to_thread(
                self.store.execute, str(cypher_query_), params
            )
        return rows, None, None

    def session(self, database: str | None = None) -> GraphTedDbSession:
        del database
        return GraphTedDbSession(self)

    async def close(self) -> None:
        return None

    def close_sync(self) -> None:
        return None

    def delete_all_indexes(self) -> Coroutine:
        async def _noop():
            return None

        return _noop()

    async def verify_connectivity(self) -> None:
        if self._http:
            try:
                await asyncio.to_thread(get_health, self._http)
                return
            except ValueError:
                rows = await asyncio.to_thread(
                    post_cypher, self._http, "RETURN 1 AS ok"
                )
        else:
            assert self.store is not None
            rows = await asyncio.to_thread(self.store.execute, "RETURN 1 AS ok")
        if not rows or rows[0].get("ok") not in (1, "1"):
            raise RuntimeError("graph-ted-db connectivity check failed")
