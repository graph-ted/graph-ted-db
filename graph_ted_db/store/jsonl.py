"""JSONL append/read with torn-line repair and atomic replace.

A hard halt during append can leave a shard with no trailing newline. The next
append would concatenate a new record onto that fragment and both lines would
fail to parse. Repair truncates the incomplete tail before any append, and on
open. See docs/format.md.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def fsync_directory(path: Path) -> None:
    """Best-effort fsync of a directory so create/rename survives a crash."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def repair_torn_jsonl(path: Path) -> int:
    """Truncate an incomplete last line (file does not end with newline).

    Returns the number of discarded bytes (0 if no repair).
    """
    if not path.is_file():
        return 0
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return 0
    last_nl = data.rfind(b"\n")
    kept = data[: last_nl + 1] if last_nl >= 0 else b""
    discarded = len(data) - len(kept)
    _replace_bytes(path, kept)
    return discarded


def iter_json_objects(
    path: Path,
    skipped: list[str] | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (line_number, object) for valid JSON objects. 1-based line numbers."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                if skipped is not None:
                    skipped.append(f"{path}:{lineno}")
                continue
            if isinstance(parsed, dict):
                yield lineno, parsed
            elif skipped is not None:
                skipped.append(f"{path}:{lineno}")


def append_jsonl(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    repair_torn_jsonl(path)
    payload = line.rstrip("\n") + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if created:
        fsync_directory(path.parent)


def replace_jsonl(path: Path, lines: Iterable[str]) -> None:
    """Atomically replace `path` with the given JSONL lines."""
    payload = "".join(line.rstrip("\n") + "\n" for line in lines)
    _replace_bytes(path, payload.encode("utf-8"))


def replace_json_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a pretty-printed JSON object file."""
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _replace_bytes(path, text.encode("utf-8"))


def _replace_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        fsync_directory(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
