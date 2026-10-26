"""Process-local exclusive lock. Never stored in the synced graph folder."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("GRAPH_TED_DB_DATA")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "graph-ted-db"


def lock_path_for(graph_id: str, data_dir: Path | None = None) -> Path:
    return (data_dir or default_data_dir()) / graph_id / "LOCK"


def index_dir_for(graph_id: str, data_dir: Path | None = None) -> Path:
    """Local derived indexes. Never stored in the synced graph folder."""
    return (data_dir or default_data_dir()) / graph_id / "index"


def writer_path_for(graph_id: str, data_dir: Path | None = None) -> Path:
    """This device's random writer id for one graph. Local only, never synced."""
    return (data_dir or default_data_dir()) / graph_id / "writer.json"


def wal_dir_for(graph_id: str, data_dir: Path | None = None) -> Path:
    """Local write-ahead log. Never stored in the synced graph folder."""
    return (data_dir or default_data_dir()) / graph_id / "wal"


def active_dir_for(graph_id: str, data_dir: Path | None = None) -> Path:
    """Growing record logs for this device. Never stored in the synced folder."""
    return (data_dir or default_data_dir()) / graph_id / "active"


def stage_dir_for(graph_id: str, data_dir: Path | None = None) -> Path:
    """Temp files for sealing a segment. Never stored in the synced folder."""
    return (data_dir or default_data_dir()) / graph_id / "stage"


def _prepare_win_lock(fd) -> None:
    fd.seek(0, os.SEEK_END)
    if fd.tell() == 0:
        fd.write("0")
        fd.flush()
    fd.seek(0)


def _lock_fd(fd) -> None:
    if sys.platform == "win32":
        import msvcrt

        _prepare_win_lock(fd)
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)


def _lock_fd_nb(fd) -> bool:
    """Take the lock if it is free. False means another holder has it."""
    if sys.platform == "win32":
        import msvcrt

        _prepare_win_lock(fd)
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_fd(fd) -> None:
    if sys.platform == "win32":
        import msvcrt

        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fd:
        _lock_fd(fd)
        try:
            yield
        finally:
            _unlock_fd(fd)


@contextmanager
def try_exclusive_lock(path: Path, timeout: float) -> Iterator[bool]:
    """Like ``exclusive_lock``, but yield False instead of waiting past ``timeout``.

    ``timeout`` of 0 tries once. The yielded value is whether this process
    holds the lock for the rest of the block.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fd:
        locked = _lock_fd_nb(fd)
        if not locked and timeout > 0:
            deadline = time.monotonic() + timeout
            while not locked and time.monotonic() < deadline:
                time.sleep(0.01)
                locked = _lock_fd_nb(fd)
        try:
            yield locked
        finally:
            if locked:
                _unlock_fd(fd)
