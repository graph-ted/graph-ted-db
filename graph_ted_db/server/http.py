"""Stdlib HTTP front for one GraphStore. Bind loopback by default."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from graph_ted_db.engine import CypherError
from graph_ted_db.store import GraphStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8099
MAX_QUERY_RECORDS = 500
MAX_BODY_BYTES = 8_000_000
HARD_MAX_RECORDS = 100_000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MUTATING_CYPHER = re.compile(
    r"\b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b",
    re.IGNORECASE,
)

logger = logging.getLogger("graph_ted_db.server")


class GraphTedHTTPServer(ThreadingHTTPServer):
    # False: on Windows, SO_REUSEADDR lets a second serve bind 8099 while an
    # old process still owns the port; curl then hits the leftover graph.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if os.name == "nt":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    def __init__(
        self,
        server_address: tuple[str, int],
        store: GraphStore,
        *,
        max_records: int = MAX_QUERY_RECORDS,
        token: str = "",
    ) -> None:
        self.store = store
        self.max_records = max_records
        self.token = token
        super().__init__(server_address, GraphTedHandler)


class GraphTedHandler(BaseHTTPRequestHandler):
    server: GraphTedHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, _health_payload(self.server.store))
            return
        if not self._authorize():
            return
        if parsed.path == "/":
            self._send_json(200, _health_payload(self.server.store))
            return
        if parsed.path == "/info":
            self._send_json(200, _info_payload(self.server.store))
            return
        if parsed.path == "/cypher":
            query, params, error = _query_from_qs(parsed.query)
            if error:
                self._send_json(400, {"error": error, "type": "request"})
                return
            if _is_mutating_cypher(query):
                self._send_json(
                    405,
                    {
                        "error": "mutating queries are not allowed on GET; use POST /cypher",
                        "type": "request",
                    },
                )
                return
            self._run_cypher(query, params, self.server.max_records)
            return
        self._send_json(404, {"error": "not found", "type": "request"})

    def do_POST(self) -> None:
        if not self._authorize():
            return
        parsed = urlparse(self.path)
        if parsed.path != "/cypher":
            self._send_json(404, {"error": "not found", "type": "request"})
            return
        body, error = self._read_json_body()
        if error:
            self._send_json(400, {"error": error, "type": "request"})
            return
        updated_by, error = _updated_by_from_body(body)
        if error:
            self._send_json(400, {"error": error, "type": "request"})
            return
        max_records = _max_records_from_body(body, self.server.max_records)
        statements_raw = body.get("statements")
        if statements_raw is not None:
            statements, error = _parse_statements(statements_raw)
            if error:
                self._send_json(400, {"error": error, "type": "request"})
                return
            self._run_statements(statements, max_records, updated_by=updated_by)
            return
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            self._send_json(400, {"error": "missing query", "type": "request"})
            return
        params = body.get("parameters", body.get("params", {}))
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self._send_json(400, {"error": "parameters must be an object", "type": "request"})
            return
        self._run_cypher(query, params, max_records, updated_by=updated_by)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, _health_payload(self.server.store), body=False)
            return
        if not self._authorize():
            return
        if parsed.path == "/":
            self._send_json(200, _health_payload(self.server.store), body=False)
            return
        self.send_error(404)

    def _authorize(self) -> bool:
        expected = self.server.token
        if not expected:
            return True
        provided = _token_from_headers(self.headers)
        if hmac.compare_digest(provided, expected):
            return True
        self._send_json(401, {"error": "unauthorized", "type": "auth"})
        return False

    def _run_cypher(
        self,
        query: str,
        params: dict[str, Any],
        max_records: int,
        *,
        updated_by: str = "cypher",
    ) -> None:
        try:
            rows = self.server.store.execute(query, params, updated_by=updated_by)
        except CypherError as exc:
            self._send_json(400, {"error": str(exc), "type": "cypher"})
            return
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc), "type": "cypher"})
            return
        except Exception:
            logger.exception("cypher failed")
            self._send_json(500, {"error": "internal error", "type": "server"})
            return
        records, truncated = _clip_rows(rows, max_records)
        self._send_json(200, {"records": records, "truncated": truncated})

    def _run_statements(
        self,
        statements: list[tuple[str, dict[str, Any]]],
        max_records: int,
        *,
        updated_by: str = "cypher",
    ) -> None:
        try:
            batches = self.server.store.execute_many(statements, updated_by=updated_by)
        except CypherError as exc:
            self._send_json(400, {"error": str(exc), "type": "cypher"})
            return
        except (TypeError, ValueError, RuntimeError) as exc:
            self._send_json(400, {"error": str(exc), "type": "cypher"})
            return
        except Exception:
            logger.exception("cypher batch failed")
            self._send_json(500, {"error": "internal error", "type": "server"})
            return
        results = []
        any_truncated = False
        for rows in batches:
            records, truncated = _clip_rows(rows, max_records)
            any_truncated = any_truncated or truncated
            results.append({"records": records, "truncated": truncated})
        self._send_json(
            200,
            {"results": results, "truncated": any_truncated},
        )

    def _read_json_body(self) -> tuple[dict[str, Any], str | None]:
        length = self.headers.get("Content-Length", "0")
        try:
            size = int(length)
        except ValueError:
            return {}, "invalid Content-Length"
        if size < 0 or size > MAX_BODY_BYTES:
            return {}, "request body too large"
        raw = self.rfile.read(size) if size else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}, "body must be JSON"
        if not isinstance(payload, dict):
            return {}, "JSON body must be an object"
        return payload, None

    def _send_json(self, status: int, payload: dict[str, Any], *, body: bool = True) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(data)


def _health_payload(store: GraphStore) -> dict[str, Any]:
    store.refresh_index()
    payload = {
        "ok": True,
        "format": store.meta.format,
        "format_version": store.meta.format_version,
        "id": store.meta.id,
        "name": store.meta.name,
        "root": str(store.root),
        "nodes": 0,
        "edges": 0,
    }
    if store._index is not None:
        payload["nodes"] = len(store._index.nodes)
        payload["edges"] = len(store._index.edges)
    return payload


def _info_payload(store: GraphStore) -> dict[str, Any]:
    store.refresh_index()
    payload = store.meta.to_dict()
    payload["root"] = str(store.root)
    if store._index is not None:
        payload["nodes"] = len(store._index.nodes)
        payload["edges"] = len(store._index.edges)
    return payload


def _json_default(value: object) -> object:
    from datetime import date, datetime
    from uuid import UUID

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _clip_rows(
    rows: list[dict[str, Any]], max_records: int
) -> tuple[list[dict[str, Any]], bool]:
    truncated = len(rows) > max_records
    if truncated:
        rows = rows[:max_records]
    return rows, truncated


def _max_records_from_body(body: dict[str, Any], default: int) -> int:
    raw = body.get("max_records")
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return HARD_MAX_RECORDS
    return min(value, HARD_MAX_RECORDS)


def _parse_statements(
    raw: Any,
) -> tuple[list[tuple[str, dict[str, Any]]], str | None]:
    if not isinstance(raw, list) or not raw:
        return [], "statements must be a non-empty array"
    out: list[tuple[str, dict[str, Any]]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"statements[{index}] must be an object"
        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            return [], f"statements[{index}] missing query"
        params = item.get("parameters", item.get("params", {}))
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return [], f"statements[{index}] parameters must be an object"
        out.append((query, params))
    return out, None


def _query_from_qs(query: str) -> tuple[str, dict[str, Any], str | None]:
    qs = parse_qs(query, keep_blank_values=True)
    raw = qs.get("query", [""])[0]
    cypher = unquote_plus(raw).strip()
    if not cypher:
        return "", {}, "missing query"
    params_raw = qs.get("params", qs.get("parameters", ["{}"]))[0]
    try:
        params = json.loads(params_raw) if params_raw else {}
    except json.JSONDecodeError:
        return "", {}, "params must be a JSON object"
    if not isinstance(params, dict):
        return "", {}, "params must be a JSON object"
    return cypher, params, None


def _is_mutating_cypher(query: str) -> bool:
    return _MUTATING_CYPHER.search(query) is not None


def _updated_by_from_body(body: dict[str, Any]) -> tuple[str, str | None]:
    raw = body.get("updated_by", "cypher")
    if raw is None:
        return "cypher", None
    if not isinstance(raw, str):
        return "", "updated_by must be a string"
    return raw, None


def _token_from_headers(headers: Any) -> str:
    auth = headers.get("Authorization", "") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (headers.get("X-Graph-Ted-Token") or "").strip()


def make_server(
    store: GraphStore,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    max_records: int = MAX_QUERY_RECORDS,
    token: str = "",
) -> GraphTedHTTPServer:
    return GraphTedHTTPServer(
        (host, port), store, max_records=max_records, token=token
    )


def serve(
    store: GraphStore,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    max_records: int = MAX_QUERY_RECORDS,
    token: str = "",
) -> None:
    token = token.strip() if token else ""
    if host not in LOOPBACK_HOSTS and not token:
        raise ValueError(
            "refusing to bind off loopback without a token "
            "(pass --token or set GRAPH_TED_DB_TOKEN)"
        )
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(store, host, port, max_records=max_records, token=token)
    bound_host, bound_port = server.server_address[:2]
    store.refresh_index()
    n_nodes = len(store._index.nodes) if store._index is not None else 0
    n_edges = len(store._index.edges) if store._index is not None else 0
    auth_note = (
        "token required except GET /health"
        if token
        else "no token (loopback)"
    )
    print(
        "graph-ted-db HTTP\n"
        f"  folder: {store.root}\n"
        f"  name:   {store.meta.name}\n"
        f"  id:     {store.meta.id}\n"
        f"  nodes:  {n_nodes}   edges: {n_edges}\n"
        f"  listen: http://{bound_host}:{bound_port}/\n"
        f"  pid:    {os.getpid()}\n"
        f"  auth:   {auth_note}\n"
        "  (POST /cypher [query | statements], GET /health)\n"
        "  If GET /health name/id do not match this banner, another process owns the port.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        server.server_close()
