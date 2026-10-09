"""Format constants and identifier helpers. See docs/format.md."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

FORMAT_NAME = "graph-ted-db"
FORMAT_VERSION = 2
# v2: each writer appends only to its own files ("<stem>.<writer>.jsonl").
LAYOUT_PER_WRITER = "per-writer"
_WRITER_RE = re.compile(r"^w[0-9a-f]{16}$")
SHARD_FANOUT = 256

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VECTOR_PROPERTY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def normalize_uuid(value: str) -> str:
    """Return a lowercase hyphenated UUID string, or raise ValueError."""
    text = value.strip().lower()
    if len(text) == 32 and "-" not in text:
        text = f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"
    try:
        parsed = UUID(text)
    except ValueError as exc:
        raise ValueError(f"not a UUID: {value!r}") from exc
    normalized = str(parsed)
    if not _UUID_RE.match(normalized):
        raise ValueError(f"not a UUID: {value!r}")
    return normalized


def shard_id(record_id: str, fanout: int = SHARD_FANOUT) -> str:
    """Shard filename stem (lowercase hex) for a record UUID."""
    if fanout != SHARD_FANOUT:
        raise ValueError(f"unsupported shard_fanout: {fanout}")
    uid = UUID(normalize_uuid(record_id))
    return f"{uid.bytes[0]:02x}"


def is_vector_property_name(name: str) -> bool:
    return bool(_VECTOR_PROPERTY_RE.match(name))


def parse_timestamp(value: str) -> datetime:
    """Parse a format-v1 UTC timestamp into an aware datetime."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"not a graph-ted-db timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime | None = None) -> str:
    """Emit UTC microseconds with a Z suffix."""
    instant = value if value is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    instant = instant.astimezone(timezone.utc)
    return instant.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_writer_id(value: str) -> bool:
    """A writer id: "w" + 16 random lowercase hex digits. Never derived from names."""
    return bool(_WRITER_RE.match(value))


def new_writer_id() -> str:
    import secrets

    return "w" + secrets.token_hex(8)
