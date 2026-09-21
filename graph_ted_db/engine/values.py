"""Runtime values for the Cypher subset: nodes, relationships, null."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from graph_ted_db.store.records import EdgeRecord, NodeRecord

_GRAPHITI_EPOCH = "1970-01-01T00:00:00+00:00"
_GRAPHITI_NODE_STRINGS = {
    "summary": "",
    "content": "",
    "source": "text",
    "source_description": "",
    "name": "",
    "group_id": "",
    "fact": "",
}
def _graphiti_prop(
    props: dict[str, Any],
    key: str,
    *,
    uuid: str,
    updated_at: str = "",
    rel_type: str = "",
    labels: tuple[str, ...] = (),
) -> Any:
    if key == "uuid":
        raw = props.get("uuid")
        return raw if raw is not None else uuid
    if key in props and props[key] is not None:
        return props[key]
    # Graphiti EntityNode.created_at is required. Do not invent invalid_at —
    # Goddard helpers treat missing invalid_at as "currently valid".
    if key == "created_at":
        return updated_at or _GRAPHITI_EPOCH
    if key == "valid_at" and "Episodic" in labels:
        return props.get("created_at") or updated_at or _GRAPHITI_EPOCH
    if key == "entity_edges" or key == "episodes":
        return []
    if key == "name" and rel_type:
        return rel_type
    if key in _GRAPHITI_NODE_STRINGS:
        return _GRAPHITI_NODE_STRINGS[key]
    return props.get(key)


@dataclass(frozen=True)
class NodeView:
    id: str
    labels: tuple[str, ...]
    props: dict[str, Any]
    updated_at: str = ""

    def has_label(self, label: str) -> bool:
        return label in self.labels

    def get(self, key: str) -> Any:
        return _graphiti_prop(
            self.props,
            key,
            uuid=self.id,
            updated_at=self.updated_at,
            labels=self.labels,
        )

    def properties(self) -> dict[str, Any]:
        out = dict(self.props)
        out.setdefault("uuid", self.id)
        for key in ("created_at", "summary", "group_id", "name"):
            if out.get(key) is None:
                value = self.get(key)
                if value is not None:
                    out[key] = value
        return out

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NodeView):
            return self.id == other.id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(frozen=True)
class RelView:
    id: str
    type: str
    from_id: str
    to_id: str
    props: dict[str, Any]
    updated_at: str = ""

    def get(self, key: str) -> Any:
        return _graphiti_prop(
            self.props,
            key,
            uuid=self.id,
            updated_at=self.updated_at,
            rel_type=self.type,
        )

    def properties(self) -> dict[str, Any]:
        out = dict(self.props)
        out.setdefault("uuid", self.id)
        for key in ("created_at", "fact", "name", "group_id", "episodes"):
            if out.get(key) is None:
                value = self.get(key)
                if value is not None:
                    out[key] = value
        return out

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RelView):
            return self.id == other.id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.id)


def node_view(record: NodeRecord) -> NodeView:
    return NodeView(
        id=record.id,
        labels=record.labels,
        props=dict(record.props),
        updated_at=record.updated_at,
    )


def rel_view(record: EdgeRecord) -> RelView:
    return RelView(
        id=record.id,
        type=record.type,
        from_id=record.from_id,
        to_id=record.to_id,
        props=dict(record.props),
        updated_at=record.updated_at,
    )


def identity_key(value: Any) -> Any:
    if isinstance(value, NodeView):
        return ("node", value.id)
    if isinstance(value, RelView):
        return ("rel", value.id)
    if isinstance(value, list):
        return ("list", tuple(identity_key(v) for v in value))
    if isinstance(value, dict):
        return ("map", tuple(sorted((k, identity_key(v)) for k, v in value.items())))
    return ("val", value)


def jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, NodeView):
        return {
            "id": value.id,
            "labels": list(value.labels),
            "properties": jsonish(value.properties()),
        }
    if isinstance(value, RelView):
        return {
            "id": value.id,
            "type": value.type,
            "from": value.from_id,
            "to": value.to_id,
            "properties": jsonish(value.properties()),
        }
    if isinstance(value, list):
        return [jsonish(v) for v in value]
    if isinstance(value, tuple):
        return [jsonish(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonish(v) for k, v in value.items()}
    return str(value)
