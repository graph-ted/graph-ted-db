"""JSONL append/read with torn-line repair and atomic replace.

A hard halt during append can leave a shard with no trailing newline. The next
append would concatenate a new record onto that fragment and both lines would
fail to parse. Repair truncates the incomplete tail before any append, and on
open. See docs/format.md.

``append_jsonl`` fsyncs that one record. A transaction instead uses
``append_batch``: every line is written, then each file is flushed and fsynced
once.
"""

from __future__ import annotations

import json
import os
import signal
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

# Set only by crash tests. SIGKILL at this point; the process does not return.
_CRASH_AT = "GRAPH_TED_DB_CRASH_AT"
_local = threading.local()


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


def crash_if(name: str) -> None:
    """SIGKILL this process when ``GRAPH_TED_DB_CRASH_AT`` equals ``name``.

    Crash tests use this to die inside a real commit. Unset, it does nothing.
    """
    if os.environ.get(_CRASH_AT) != name:
        return
    os.kill(os.getpid(), signal.SIGKILL)


def _die_during_fsync(fd: int) -> None:
    """SIGKILL during ``os.fsync``. Does not return."""
    try:
        pid = os.fork()
    except OSError:
        os.kill(os.getpid(), signal.SIGKILL)
        return
    if pid == 0:
        os.kill(os.getppid(), signal.SIGKILL)
        os._exit(0)
    os.fsync(fd)
    os.kill(os.getpid(), signal.SIGKILL)


def _fsync_fd(fd: int, *, crash_at: str | None = None) -> None:
    if crash_at is not None and os.environ.get(_CRASH_AT) == crash_at:
        _die_during_fsync(fd)
    os.fsync(fd)


class _AppendBatch:
    """One open append per file. ``finish`` flush+fsyncs each file once."""

    def __init__(self) -> None:
        self._files: dict[Path, tuple[TextIO, bool]] = {}

    def append(self, path: Path, line: str) -> None:
        key = path.resolve()
        slot = self._files.get(key)
        if slot is None:
            key.parent.mkdir(parents=True, exist_ok=True)
            created = not key.exists()
            repair_torn_jsonl(key)
            handle: TextIO = key.open("a", encoding="utf-8", newline="\n")
            slot = (handle, created)
            self._files[key] = slot
        handle = slot[0]
        handle.write(line.rstrip("\n") + "\n")

    def finish(self) -> None:
        items = list(self._files.items())
        self._files.clear()
        dirs: set[Path] = set()
        for index, (path, (handle, created)) in enumerate(items):
            handle.flush()
            _fsync_fd(handle.fileno(), crash_at="data-fsync" if index == 0 else None)
            handle.close()
            if created:
                dirs.add(path.parent)
        for directory in dirs:
            fsync_directory(directory)

    def discard(self) -> None:
        items = list(self._files.values())
        self._files.clear()
        for handle, _created in items:
            try:
                handle.close()
            except OSError:
                pass


@contextmanager
def append_batch() -> Iterator[None]:
    """Group appends and fsync each file once when the block exits cleanly."""
    current = getattr(_local, "batch", None)
    if current is not None:
        yield
        return
    batch = _AppendBatch()
    _local.batch = batch
    try:
        yield
    except BaseException:
        batch.discard()
        raise
    else:
        batch.finish()
    finally:
        _local.batch = None


def append_jsonl(path: Path, line: str) -> None:
    batch = getattr(_local, "batch", None)
    if isinstance(batch, _AppendBatch):
        batch.append(path, line)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    repair_torn_jsonl(path)
    payload = line.rstrip("\n") + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        # One complete line is the commit. A torn tail is truncated on open.
        _fsync_fd(handle.fileno(), crash_at="data-fsync")
    if created:
        fsync_directory(path.parent)


def replace_jsonl(
    path: Path,
    lines: Iterable[str],
    *,
    crash_at: str | None = None,
) -> None:
    """Atomically replace `path` with the given JSONL lines."""
    payload = "".join(line.rstrip("\n") + "\n" for line in lines)
    _replace_bytes(path, payload.encode("utf-8"), crash_at=crash_at)


def replace_json_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a pretty-printed JSON object file."""
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _replace_bytes(path, text.encode("utf-8"))


def _replace_bytes(path: Path, data: bytes, *, crash_at: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            _fsync_fd(handle.fileno(), crash_at=crash_at)
        os.replace(tmp, path)
        fsync_directory(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
