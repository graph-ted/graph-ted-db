"""App metadata files under ``meta/<namespace>/``, one file per writer.

For small documents an application keeps next to the graph (for example
versioned ontology catalogs, ``meta/ontology/<group>/v3.<writer>.json``). The
same rule as record files applies: a device only ever creates or replaces its
*own* files (``<name>.<writer-id>.json``), so a sync client never has to merge
two devices' edits of one file. Readers see every writer's copy and decide.

Files are written atomically (temp file + rename). By default an existing file
is never replaced, which suits append-only version histories.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graph_ted_db.store.format import is_writer_id

# Store-owned names directly under meta/; apps cannot use them as a namespace.
RESERVED = frozenset({"writers", "labels.json", "deleted", "deleted.jsonl"})

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_META_FILE = re.compile(r"^(?P<name>.+)\.(?P<writer>[^.]+)\.json$")


class MetaPathError(ValueError):
    """A meta directory or name is not a safe relative path."""


@dataclass(frozen=True)
class MetaEntry:
    """One file in a meta directory.

    ``problem`` is None for a readable file written by a known-format writer,
    otherwise one of ``cloud-only``, ``empty``, ``unterminated`` (still
    syncing?), ``invalid-json``, ``conflict-copy`` (a name the store never
    writes, e.g. ``v3.<writer> (1).json``), ``tmp``. ``data`` is set only when
    ``problem`` is None.
    """

    path: Path
    name: str | None
    writer: str | None
    data: dict[str, Any] | None
    problem: str | None


def check_segments(parts: str | list[str]) -> list[str]:
    segs = parts.split("/") if isinstance(parts, str) else list(parts)
    if not segs or any(
        not _SEGMENT.match(s) or s in (".", "..") or s.endswith(".tmp") for s in segs
    ):
        raise MetaPathError(f"unsafe meta path {parts!r}")
    return segs


def meta_path(meta_dir: Path, directory: str, name: str, writer: str) -> Path:
    segs = check_segments(directory)
    if segs[0] in RESERVED:
        raise MetaPathError(f"meta/{segs[0]} is reserved for the store")
    check_segments([name])
    if not is_writer_id(writer):
        raise MetaPathError(f"not a writer id: {writer!r}")
    return meta_dir.joinpath(*segs) / f"{name}.{writer}.json"


def parse_name(file_name: str) -> tuple[str, str] | None:
    m = _META_FILE.match(file_name)
    if not m or not is_writer_id(m["writer"]):
        return None
    return m["name"], m["writer"]


def read_entry(path: Path, *, cloud_only: bool = False) -> MetaEntry:
    if path.name.endswith(".tmp"):
        return MetaEntry(path, None, None, None, "tmp")
    parsed = parse_name(path.name)
    if parsed is None:
        return MetaEntry(path, None, None, None, "conflict-copy")
    name, writer = parsed
    if cloud_only:
        return MetaEntry(path, name, writer, None, "cloud-only")
    raw = path.read_bytes()
    if not raw:
        return MetaEntry(path, name, writer, None, "empty")
    if not raw.endswith(b"\n"):
        # The store always ends a meta file with a newline; a missing one is a
        # partial download.
        return MetaEntry(path, name, writer, None, "unterminated")
    try:
        data = json.loads(raw)
    except ValueError:
        return MetaEntry(path, name, writer, None, "invalid-json")
    if not isinstance(data, dict):
        return MetaEntry(path, name, writer, None, "invalid-json")
    return MetaEntry(path, name, writer, data, None)


def app_meta_dirs(meta_dir: Path) -> list[Path]:
    """Every directory under meta/ that belongs to applications."""
    if not meta_dir.is_dir():
        return []
    out: list[Path] = []
    for top in sorted(meta_dir.iterdir()):
        if top.is_dir() and top.name not in RESERVED:
            out.append(top)
            out.extend(sorted(p for p in top.rglob("*") if p.is_dir()))
    return out
