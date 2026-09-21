"""Process-local exclusive lock. Never stored in the synced graph folder."""

from __future__ import annotations

import os
import sys
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


def wal_dir_for(graph_id: str, data_dir: Path | None = None) -> Path:
    """Local write-ahead log. Never stored in the synced graph folder."""
    return (data_dir or default_data_dir()) / graph_id / "wal"


def _lock_fd(fd) -> None:
    if os.name == "nt":
        import msvcrt

        fd.seek(0, os.SEEK_END)
        if fd.tell() == 0:
            fd.write("0")
            fd.flush()
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)


def _unlock_fd(fd) -> None:
    if os.name == "nt":
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
