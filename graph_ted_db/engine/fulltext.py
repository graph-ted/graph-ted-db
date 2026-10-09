"""Naive fulltext over node/edge properties for CALL db.index.fulltext.* procedures."""

from __future__ import annotations

import re
from typing import Any

from graph_ted_db.engine.values import RelView, node_view, rel_view

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_STOP = frozenset({"and", "or", "not"})

NODE_INDEX_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "node_name_and_summary": (("Entity",), ("name", "summary", "group_id")),
    "community_name": (("Community",), ("name", "group_id")),
    "episode_content": (
        ("Episodic",),
        ("content", "source", "source_description", "group_id", "name"),
    ),
}

EDGE_INDEX_FIELDS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "edge_name_and_fact": ("RELATES_TO", ("name", "fact", "group_id")),
}


def tokens(query: str) -> list[str]:
    found = [part.lower() for part in _TOKEN.findall(query or "")]
    return [part for part in found if part not in _STOP]


def score_text(haystack: str, query_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    blob = haystack.lower()
    hits = sum(1 for token in query_tokens if token in blob)
    return hits / len(query_tokens)


def _field_blob(props: dict[str, Any], fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in fields:
        value = props.get(key)
        if value is None:
            continue
        parts.append(str(value))
    return " ".join(parts)


def query_nodes(index, index_name: str, query: str, limit: int | None) -> list[dict]:
    labels, fields = NODE_INDEX_FIELDS.get(
        index_name,
        (("Entity", "Episodic", "Community"), ("name", "summary", "content", "group_id")),
    )
    query_tokens = tokens(query)
    scored: list[tuple[float, Any]] = []
    for record in index.nodes.values():
        if labels and not any(label in record.labels for label in labels):
            continue
        value = score_text(_field_blob(record.props, fields), query_tokens)
        if value <= 0:
            continue
        scored.append((value, record))
    scored.sort(key=lambda item: item[0], reverse=True)
    if limit is not None:
        scored = scored[: max(0, int(limit))]
    return [{"node": node_view(record), "score": value} for value, record in scored]


def query_relationships(index, index_name: str, query: str, limit: int | None) -> list[dict]:
    rel_type, fields = EDGE_INDEX_FIELDS.get(index_name, (None, ("name", "fact", "group_id")))
    query_tokens = tokens(query)
    scored: list[tuple[float, RelView]] = []
    for record in index.edges.values():
        if rel_type and record.type != rel_type:
            continue
        value = score_text(_field_blob(record.props, fields), query_tokens)
        if value <= 0:
            continue
        scored.append((value, rel_view(record)))
    scored.sort(key=lambda item: item[0], reverse=True)
    if limit is not None:
        scored = scored[: max(0, int(limit))]
    return [{"relationship": view, "node": view, "score": value} for value, view in scored]
