"""Typed records for format v1. No I/O."""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from graph_ted_db.store.format import (
    FORMAT_NAME,
    FORMAT_VERSION,
    SHARD_FANOUT,
    format_timestamp,
    is_vector_property_name,
    normalize_uuid,
    parse_timestamp,
)

Kind = Literal["node", "edge", "vector"]


def _require_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _require_props(data: Mapping[str, Any]) -> dict[str, Any]:
    props = data.get("props", {})
    if not isinstance(props, dict):
        raise ValueError("props must be an object")
    return dict(props)


def _record_version(data: Mapping[str, Any]) -> int:
    v = data.get("v", 1)
    if not isinstance(v, int) or v < 1:
        raise ValueError("v must be a positive int")
    return v


_GRAPH_META_KEYS = frozenset(
    {"format", "format_version", "id", "name", "created_at", "shard_fanout"}
)


@dataclass(frozen=True)
class GraphMeta:
    id: str
    name: str
    created_at: str
    format: str = FORMAT_NAME
    format_version: int = FORMAT_VERSION
    shard_fanout: int = SHARD_FANOUT
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format": self.format,
            "format_version": self.format_version,
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "shard_fanout": self.shard_fanout,
        }
        for key, value in self.extras.items():
            if key not in payload:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GraphMeta:
        if data.get("format") != FORMAT_NAME:
            raise ValueError(f"unsupported format: {data.get('format')!r}")
        version = data.get("format_version")
        if not isinstance(version, int) or version < 1:
            raise ValueError(f"invalid format_version: {version!r}")
        if version > FORMAT_VERSION:
            raise ValueError(
                f"graph format_version {version} is newer than this library "
                f"({FORMAT_VERSION})"
            )
        fanout = data.get("shard_fanout", SHARD_FANOUT)
        if fanout != SHARD_FANOUT:
            raise ValueError(f"unsupported shard_fanout: {fanout}")
        extras = {key: value for key, value in data.items() if key not in _GRAPH_META_KEYS}
        return cls(
            id=normalize_uuid(_require_str(data, "id")),
            name=_require_str(data, "name"),
            created_at=format_timestamp(parse_timestamp(_require_str(data, "created_at"))),
            format=FORMAT_NAME,
            format_version=version,
            shard_fanout=fanout,
            extras=extras,
        )


@dataclass(frozen=True)
class NodeRecord:
    id: str
    updated_at: str
    labels: tuple[str, ...]
    props: dict[str, Any]
    updated_by: str = ""
    v: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "v": self.v,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "labels": list(self.labels),
            "props": self.props,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeRecord:
        labels = data.get("labels", [])
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise ValueError("labels must be an array of strings")
        return cls(
            id=normalize_uuid(_require_str(data, "id")),
            v=_record_version(data),
            updated_at=format_timestamp(parse_timestamp(_require_str(data, "updated_at"))),
            updated_by=_optional_str(data, "updated_by"),
            labels=tuple(labels),
            props=_require_props(data),
        )


@dataclass(frozen=True)
class EdgeRecord:
    id: str
    updated_at: str
    type: str
    from_id: str
    to_id: str
    props: dict[str, Any]
    updated_by: str = ""
    v: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "v": self.v,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "type": self.type,
            "from": self.from_id,
            "to": self.to_id,
            "props": self.props,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EdgeRecord:
        return cls(
            id=normalize_uuid(_require_str(data, "id")),
            v=_record_version(data),
            updated_at=format_timestamp(parse_timestamp(_require_str(data, "updated_at"))),
            updated_by=_optional_str(data, "updated_by"),
            type=_require_str(data, "type"),
            from_id=normalize_uuid(_require_str(data, "from")),
            to_id=normalize_uuid(_require_str(data, "to")),
            props=_require_props(data),
        )


@dataclass(frozen=True)
class VectorRecord:
    id: str
    updated_at: str
    dim: int
    vec_b64: str
    property: str
    updated_by: str = ""
    v: int = 1
    dtype: str = "f32le"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "v": self.v,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "dim": self.dim,
            "dtype": self.dtype,
            "vec": self.vec_b64,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    def floats(self) -> tuple[float, ...]:
        raw = base64.b64decode(self.vec_b64, validate=True)
        expected = self.dim * 4
        if len(raw) != expected:
            raise ValueError(
                f"vector {self.id} decoded to {len(raw)} bytes, expected {expected}"
            )
        return struct.unpack(f"<{self.dim}f", raw)

    @classmethod
    def from_floats(
        cls,
        *,
        id: str,
        property: str,
        values: list[float] | tuple[float, ...],
        updated_at: str | datetime,
        updated_by: str = "",
        v: int = 1,
    ) -> VectorRecord:
        dim = len(values)
        raw = struct.pack(f"<{dim}f", *values)
        ts = (
            updated_at
            if isinstance(updated_at, str)
            else format_timestamp(updated_at)
        )
        return cls(
            id=normalize_uuid(id),
            updated_at=format_timestamp(parse_timestamp(ts)),
            dim=dim,
            vec_b64=base64.b64encode(raw).decode("ascii"),
            property=property,
            updated_by=updated_by,
            v=v,
            dtype="f32le",
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], property: str) -> VectorRecord:
        if not is_vector_property_name(property):
            raise ValueError(f"invalid vector property name: {property!r}")
        dim = data.get("dim")
        if not isinstance(dim, int) or dim < 1:
            raise ValueError("dim must be a positive int")
        dtype = data.get("dtype", "f32le")
        if dtype != "f32le":
            raise ValueError(f"unsupported dtype: {dtype!r}")
        if "vec" in data and isinstance(data["vec"], list):
            raise ValueError("vec must be base64 f32le, not a JSON array")
        vec = _require_str(data, "vec")
        try:
            raw = base64.b64decode(vec, validate=True)
        except Exception as exc:
            raise ValueError("vec is not valid base64") from exc
        if len(raw) != dim * 4:
            raise ValueError(
                f"vec length {len(raw)} does not match dim {dim} (expected {dim * 4})"
            )
        return cls(
            id=normalize_uuid(_require_str(data, "id")),
            v=_record_version(data),
            updated_at=format_timestamp(parse_timestamp(_require_str(data, "updated_at"))),
            updated_by=_optional_str(data, "updated_by"),
            dim=dim,
            vec_b64=vec,
            property=property,
            dtype="f32le",
        )


@dataclass(frozen=True)
class Tombstone:
    id: str
    kind: Kind
    updated_at: str
    updated_by: str = ""
    v: int = 1
    property: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "v": self.v,
            "kind": self.kind,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }
        if self.kind == "vector":
            payload["property"] = self.property
        return payload

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Tombstone:
        kind = data.get("kind")
        if kind not in ("node", "edge", "vector"):
            raise ValueError(f"invalid tombstone kind: {kind!r}")
        property_name: str | None = None
        if kind == "vector":
            property_name = _require_str(data, "property")
            if not is_vector_property_name(property_name):
                raise ValueError(f"invalid vector property name: {property_name!r}")
        return cls(
            id=normalize_uuid(_require_str(data, "id")),
            kind=kind,
            updated_at=format_timestamp(parse_timestamp(_require_str(data, "updated_at"))),
            updated_by=_optional_str(data, "updated_by"),
            v=_record_version(data),
            property=property_name,
        )


EMPTY_LABELS: dict[str, list[str]] = {
    "node_labels": [],
    "relationship_types": [],
    "vector_properties": [],
}
