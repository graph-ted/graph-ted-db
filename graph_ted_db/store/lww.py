"""Per-record last-write-wins comparison. See docs/format.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from graph_ted_db.store.format import parse_timestamp
from graph_ted_db.store.records import EdgeRecord, NodeRecord, Tombstone, VectorRecord

LiveRecord = NodeRecord | EdgeRecord | VectorRecord


@dataclass(frozen=True)
class LwwCandidate:
    updated_at: datetime
    is_tombstone: bool
    v: int
    json_line: str
    payload: LiveRecord | Tombstone


def candidate_from_live(record: LiveRecord) -> LwwCandidate:
    return LwwCandidate(
        updated_at=parse_timestamp(record.updated_at),
        is_tombstone=False,
        v=record.v,
        json_line=record.to_jsonl(),
        payload=record,
    )


def candidate_from_tombstone(record: Tombstone) -> LwwCandidate:
    return LwwCandidate(
        updated_at=parse_timestamp(record.updated_at),
        is_tombstone=True,
        v=record.v,
        json_line=record.to_jsonl(),
        payload=record,
    )


def _sort_key(c: LwwCandidate) -> tuple[Any, ...]:
    # Greater tuple wins. Tombstone-beats-live is encoded as 1 vs 0.
    return (
        c.updated_at,
        1 if c.is_tombstone else 0,
        c.v,
        c.json_line,
    )


def winner(*candidates: LwwCandidate) -> LwwCandidate:
    if not candidates:
        raise ValueError("winner() requires at least one candidate")
    return max(candidates, key=_sort_key)


def resolve(
    lives: list[LiveRecord],
    tombs: list[Tombstone],
) -> LwwCandidate | None:
    """Pick the LWW winner among live versions and tombstones. None if empty."""
    candidates = [candidate_from_live(r) for r in lives] + [
        candidate_from_tombstone(t) for t in tombs
    ]
    if not candidates:
        return None
    return winner(*candidates)
