"""Seal a finished record log into an immutable file in the synced folder.

The synced tree must only gain files that are complete the moment their name
appears. Bytes are written and fsynced in the local stage directory (never
inside the graph folder). ``os.replace`` into the destination is atomic when
both paths are on the same filesystem, so a sync client cannot observe a
partial file and cannot see the file grow afterwards.

See docs/format.md, "Sealed segments".
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from graph_ted_db.store.jsonl import fsync_directory
from graph_ted_db.store.paths import bundle_filename, segment_filename, segment_seq


@dataclass(frozen=True)
class PublishPolicy:
    """When a writer's local active log is sealed into the synced folder.

    A record is durable on this device as soon as the active log fsyncs. Other
    devices see it only after a seal and after their sync client copies the
    new segment. The library lag (not counting the sync client) is:

    * at most ``max_interval`` seconds while writes keep arriving, or sooner
      when the active log reaches ``max_bytes``;
    * at most ``idle`` seconds after the last write;
    * none after ``GraphStore.publish`` or ``GraphStore.close``, and on the
      next ``open`` for bytes left behind by a crash.

    ``0`` disables that one trigger. Triggers apply to the whole directory
    (every shard of ``nodes/``, or of ``edges/``, …), and one publish writes
    one file for that directory. ``compact_after`` is how many of those files
    this writer keeps before folding them into one. ``0`` disables automatic
    compaction (``compact()`` still works).

    Environment overrides (used when ``GraphStore.open`` is not passed a
    policy): ``GRAPH_TED_DB_PUBLISH_BYTES``, ``GRAPH_TED_DB_PUBLISH_INTERVAL``,
    ``GRAPH_TED_DB_PUBLISH_IDLE``, ``GRAPH_TED_DB_COMPACT_AFTER``.
    """

    max_bytes: int = 1_048_576
    max_interval: float = 2.0
    idle: float = 0.5
    compact_after: int = 4

    def __post_init__(self) -> None:
        if self.max_bytes < 0 or self.compact_after < 0:
            raise ValueError("publish policy counts must be >= 0")
        if self.max_interval < 0 or self.idle < 0:
            raise ValueError("publish policy durations must be >= 0")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> PublishPolicy:
        source = os.environ if env is None else env

        def as_int(name: str, default: int) -> int:
            raw = source.get(name)
            if raw is None or raw.strip() == "":
                return default
            return int(raw)

        def as_float(name: str, default: float) -> float:
            raw = source.get(name)
            if raw is None or raw.strip() == "":
                return default
            return float(raw)

        return cls(
            max_bytes=as_int("GRAPH_TED_DB_PUBLISH_BYTES", cls.max_bytes),
            max_interval=as_float("GRAPH_TED_DB_PUBLISH_INTERVAL", cls.max_interval),
            idle=as_float("GRAPH_TED_DB_PUBLISH_IDLE", cls.idle),
            compact_after=as_int("GRAPH_TED_DB_COMPACT_AFTER", cls.compact_after),
        )


def due(
    policy: PublishPolicy,
    *,
    size: int,
    age: float,
    quiet: float,
    force: bool,
) -> bool:
    """Whether an active log of this size and age should be sealed now."""
    if force:
        return size > 0
    if size <= 0:
        return False
    if policy.max_bytes and size >= policy.max_bytes:
        return True
    if policy.max_interval and age >= policy.max_interval:
        return True
    return bool(policy.idle) and quiet >= policy.idle


def next_seq(directory: Path, stem: str, writer: str) -> int:
    """Next 1-based sequence for this writer and stem. Existing names are skipped."""
    best = 0
    if directory.is_dir():
        prefix = f"{stem}.{writer}."
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        for name in names:
            if not name.startswith(prefix):
                continue
            seq = segment_seq(name)
            if seq is not None and seq > best:
                best = seq
    return best + 1


def segment_path(directory: Path, stem: str, writer: str, seq: int) -> Path:
    return directory / segment_filename(stem, writer, seq)


def next_bundle_seq(directory: Path, writer: str) -> int:
    """Next 1-based sequence for this writer's bundles in `directory`."""
    best = 0
    if not directory.is_dir():
        return 1
    try:
        names = os.listdir(directory)
    except OSError:
        return 1
    for name in names:
        seq = segment_seq(name)
        if seq is not None and name == bundle_filename(writer, seq):
            best = max(best, seq)
    return best + 1


def bundle_path(directory: Path, writer: str, seq: int) -> Path:
    return directory / bundle_filename(writer, seq)


def publish_immutable(dest: Path, data: bytes, *, stage_dir: Path) -> None:
    """Create ``dest`` with its final bytes in one rename.

    ``dest`` must not already exist. The name is never opened for append.
    ``stage_dir`` must be outside the synced graph folder.

    Same filesystem: one ``os.replace`` from the stage file, which is atomic.
    Across filesystems: Linux ``O_TMPFILE`` (no directory entry until the
    bytes are complete), otherwise one write of the already-complete buffer
    to a ``.tmp`` name readers ignore, fsync, then rename. The destination
    name itself appears only after that rename.
    """
    if not data:
        raise ValueError("refusing to publish an empty segment")
    if dest.exists():
        raise FileExistsError(f"segment already exists: {dest}")
    stage_dir.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = stage_dir / f"{dest.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    try:
        _write_fsync(tmp, data)
        try:
            os.replace(tmp, dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            _publish_across_devices(dest, data)
        fsync_directory(dest.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_fsync(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        view = memoryview(data)
        while view:
            written = handle.write(view)
            view = view[written:]
        handle.flush()
        os.fsync(handle.fileno())


def _publish_across_devices(dest: Path, data: bytes) -> None:
    if _try_otmpfile(dest, data):
        return
    local = dest.with_name(f"{dest.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        _write_fsync(local, data)
        os.replace(local, dest)
    finally:
        if local.exists():
            try:
                local.unlink()
            except OSError:
                pass


def _try_otmpfile(dest: Path, data: bytes) -> bool:
    """Link a nameless complete file into ``dest``. Linux only; False if unsupported."""
    flags = getattr(os, "O_TMPFILE", None)
    if flags is None:
        return False
    try:
        fd = os.open(dest.parent, flags | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written == 0:
                return False
            view = view[written:]
        os.fsync(fd)
        try:
            os.link(f"/proc/self/fd/{fd}", dest)
        except OSError:
            return False
        return True
    finally:
        os.close(fd)


def truncate_file(path: Path) -> None:
    """Empty a local active log and fsync that truncation."""
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
