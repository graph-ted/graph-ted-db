"""HTTP query client for graph-ted-db serve. No graphiti-core dependency."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT_SECONDS = 120.0


def to_jsonable(value: Any) -> Any:
    from datetime import date, datetime
    from uuid import UUID

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return to_jsonable(tolist())
    return str(value)


def normalize_base(url: str) -> str:
    base = url.strip().rstrip("/")
    if base.lower().endswith("/cypher"):
        base = base[: -len("/cypher")].rstrip("/")
    return base


def is_http_url(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith(("http://", "https://"))


def _post(
    base_url: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    url = f"{normalize_base(base_url)}/cypher"
    body = json.dumps(to_jsonable(payload)).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"graph-ted-db HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ValueError(f"graph-ted-db unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("graph-ted-db returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("graph-ted-db response must be a JSON object")
    return parsed


def get_health(base_url: str, timeout: float = 30.0) -> dict[str, Any]:
    url = f"{normalize_base(base_url)}/health"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"graph-ted-db health failed: {exc}") from exc
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise ValueError("graph-ted-db health check failed")
    return parsed


def post_cypher(
    base_url: str,
    query: str,
    parameters: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_records: int | None = None,
    updated_by: str | None = None,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"query": query, "parameters": parameters or {}}
    if max_records is not None:
        payload["max_records"] = max_records
    if updated_by is not None:
        payload["updated_by"] = updated_by
    body = _post(base_url, payload, timeout)
    records = body.get("records")
    if not isinstance(records, list):
        raise ValueError("graph-ted-db response missing records list")
    return [_fill_graphiti_row(row) for row in records]


def post_cypher_many(
    base_url: str,
    statements: list[tuple[str, dict[str, Any]]],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_records: int | None = None,
    updated_by: str | None = None,
) -> list[list[dict[str, Any]]]:
    if not statements:
        return []
    payload: dict[str, Any] = {
        "statements": [
            {"query": query, "parameters": params} for query, params in statements
        ]
    }
    if max_records is not None:
        payload["max_records"] = max_records
    if updated_by is not None:
        payload["updated_by"] = updated_by
    body = _post(base_url, payload, timeout)
    results = body.get("results")
    if not isinstance(results, list):
        raise ValueError("graph-ted-db batch response missing results list")
    rows: list[list[dict[str, Any]]] = []
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("records"), list):
            raise ValueError("graph-ted-db batch result missing records list")
        rows.append([_fill_graphiti_row(row) for row in item["records"]])
    return rows


def _fill_graphiti_row(row: Any) -> Any:
    """If Graphiti asked for a column and got JSON null, supply its default."""
    if not isinstance(row, dict):
        return row
    defaults = {
        "created_at": "1970-01-01T00:00:00+00:00",
        "summary": "",
        "content": "",
        "source": "text",
        "source_description": "",
        "fact": "",
        "attributes": {},
        "labels": [],
        "entity_edges": [],
        "episodes": [],
    }
    out = dict(row)
    for key, default in defaults.items():
        if key in out and out[key] is None:
            out[key] = default
    return out
