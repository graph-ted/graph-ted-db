"""Windows: a fresh, empty LOCK file whose byte 0 another process holds.

Two processes open a brand-new LOCK at once. Both can see it empty; the
first locks byte 0 (Windows allows locking past end of file), and the
second must not then fail writing the lock byte (PermissionError 13).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from graph_ted_db.store.lock import exclusive_lock, try_exclusive_lock

_HOLDER = """
import msvcrt, sys
fd = open(sys.argv[1], "a+", encoding="utf-8")
fd.seek(0)
msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
print("ready", flush=True)
sys.stdin.readline()
fd.seek(0)
msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
fd.close()
"""


@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt byte-range locks")
def test_empty_lock_file_held_by_other_process(tmp_path: Path) -> None:
    lock = tmp_path / "LOCK"
    lock.write_bytes(b"")  # created but not yet given its lock byte
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        with try_exclusive_lock(lock, timeout=0) as got:
            assert got is False  # busy, not PermissionError
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=30) == 0
    with exclusive_lock(lock):
        pass
    with try_exclusive_lock(lock, timeout=0) as got:
        assert got is True


def test_lock_file_gets_its_byte(tmp_path: Path) -> None:
    lock = tmp_path / "sub" / "LOCK"
    with exclusive_lock(lock):
        pass
    assert lock.stat().st_size >= 1 or sys.platform != "win32"
    with try_exclusive_lock(lock, timeout=0) as got:
        assert got is True
