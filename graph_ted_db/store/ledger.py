"""This writer's publish ledger: what it sealed into the synced folder (BUG-007).

Local only, next to the active logs, never synced. One append-only JSON line
per event::

    {"dir": "nodes", "name": "bundle.<writer>.3.jsonl", "seq": 3,
     "size": 1234, "sha256": "...", "state": "live" | "retired"}

A ``live`` line is written when a sealed file is published, together with a
retained copy of its bytes (``sealed/<dir>/<name>``). A ``retired`` line is
written when this writer replaces the file on purpose (compaction), and the
retained copy is dropped. The last line for a name wins.

The ledger lets the writer:

- never reuse a published name (the seq high-water survives a file that a
  sync client deleted);
- put back a live file that disappeared from the folder, with the same name
  and the same bytes, so every replica converges.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from graph_ted_db.store.jsonl import fsync_directory, has_unterminated_tail, repair_torn_jsonl
from graph_ted_db.store.paths import segment_seq

log = logging.getLogger(__name__)

LIVE = "live"
RETIRED = "retired"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    dir: str
    name: str
    seq: int
    size: int
    sha256: str
    state: str

    @property
    def key(self) -> str:
        return f"{self.dir}/{self.name}" if self.dir else self.name


class PublishLedger:
    """Append-only ledger of this writer's sealed files, plus retained copies."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.file = root / "ledger.jsonl"
        self.copies = root / "sealed"
        self._entries: dict[str, LedgerEntry] | None = None

    def exists(self) -> bool:
        return self.file.is_file()

    def _load(self) -> dict[str, LedgerEntry]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, LedgerEntry] = {}
        if self.file.is_file():
            if has_unterminated_tail(self.file):
                repair_torn_jsonl(self.file)
            with self.file.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    try:
                        row = json.loads(raw)
                        entry = LedgerEntry(
                            dir=str(row["dir"]),
                            name=str(row["name"]),
                            seq=int(row["seq"]),
                            size=int(row["size"]),
                            sha256=str(row["sha256"]),
                            state=str(row["state"]),
                        )
                    except (ValueError, KeyError, TypeError):
                        continue
                    entries[entry.key] = entry
        self._entries = entries
        return entries

    def entries(self) -> list[LedgerEntry]:
        return list(self._load().values())

    def live(self) -> list[LedgerEntry]:
        return [e for e in self._load().values() if e.state == LIVE]

    def get(self, rel_dir: str, name: str) -> LedgerEntry | None:
        return self._load().get(f"{rel_dir}/{name}" if rel_dir else name)

    def high_water(self, rel_dir: str, prefix: str) -> int:
        """Greatest seq ever recorded in ``rel_dir`` for names starting with ``prefix``."""
        best = 0
        for entry in self._load().values():
            if entry.dir == rel_dir and entry.name.startswith(prefix):
                best = max(best, entry.seq)
        return best

    def copy_path(self, entry: LedgerEntry) -> Path:
        return self.copies.joinpath(*Path(entry.dir).parts, entry.name)

    def retained(self, entry: LedgerEntry) -> bytes | None:
        """The retained bytes for ``entry``, or None if missing or not matching the ledger."""
        try:
            data = self.copy_path(entry).read_bytes()
        except OSError:
            return None
        if len(data) != entry.size or sha256_hex(data) != entry.sha256:
            return None
        return data

    def _append(self, entry: LedgerEntry) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "dir": entry.dir,
                "name": entry.name,
                "seq": entry.seq,
                "size": entry.size,
                "sha256": entry.sha256,
                "state": entry.state,
            },
            sort_keys=True,
        )
        new = not self.file.exists()
        with self.file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if new:
            fsync_directory(self.root)
        self._load()[entry.key] = entry

    def record(self, rel_dir: str, name: str, data: bytes) -> LedgerEntry:
        """Retain ``data`` and mark ``rel_dir/name`` live. Copy first, then the line."""
        entry = LedgerEntry(
            dir=rel_dir,
            name=name,
            seq=segment_seq(name) or 0,
            size=len(data),
            sha256=sha256_hex(data),
            state=LIVE,
        )
        dest = self.copy_path(entry)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".{os.getpid()}.part")
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
        fsync_directory(dest.parent)
        self._append(entry)
        return entry

    def retire(self, rel_dir: str, name: str) -> None:
        """Mark a live file as replaced on purpose. It is never restored after this."""
        entry = self.get(rel_dir, name)
        if entry is None or entry.state != LIVE:
            return
        self._append(
            LedgerEntry(
                dir=entry.dir,
                name=entry.name,
                seq=entry.seq,
                size=entry.size,
                sha256=entry.sha256,
                state=RETIRED,
            )
        )
        try:
            self.copy_path(entry).unlink()
        except OSError:
            pass
