"""Read/write a graph folder: shards, tombstones, conflict-copy union, LWW."""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import stat as stat_mod
import threading
import time
import weakref
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from graph_ted_db.index.local import LocalIndex
from graph_ted_db.store.aliases import _GraphStoreAliases
from graph_ted_db.store.format import (
    FORMAT_NAME,
    FORMAT_VERSION,
    LAYOUT_BUNDLES,
    format_timestamp,
    is_vector_property_name,
    is_writer_id,
    normalize_uuid,
    shard_id,
)
from graph_ted_db.store.init import load_graph_meta, load_or_create_writer_id
from graph_ted_db.store.jsonl import (
    append_batch,
    append_jsonl,
    crash_if,
    fsync_directory,
    has_unterminated_tail,
    in_append_batch,
    iter_json_objects,
    repair_torn_jsonl,
    replace_json_file,
    replace_jsonl,
)
from graph_ted_db.store.lock import (
    active_dir_for,
    exclusive_lock,
    index_dir_for,
    lock_path_for,
    stage_dir_for,
    wal_dir_for,
    writer_path_for,
)
from graph_ted_db.store.lww import resolve
from graph_ted_db.store.meta import (
    MetaEntry,
    app_meta_dirs,
    check_segments,
    meta_path,
    read_entry,
)
from graph_ted_db.store.paths import (
    GraphPaths,
    bundle_jsonl_files,
    discover_shard_stems,
    is_bundle_name,
    is_legacy_v2_name,
    is_per_shard_segment_name,
    is_record_file_name,
    is_sealed_segment_name,
    segment_stem,
    shard_jsonl_files,
    writer_of,
)
from graph_ted_db.store.records import (
    EMPTY_LABELS,
    EdgeRecord,
    GraphMeta,
    NodeRecord,
    Tombstone,
    VectorRecord,
)
from graph_ted_db.store.segments import (
    PublishPolicy,
    bundle_path,
    due,
    next_bundle_seq,
    next_seq,
    publish_immutable,
    segment_path,
    truncate_file,
)

log = logging.getLogger("graph_ted_db")

# Stores still referenced at process exit. A killed process does not run this;
# the next open seals whatever the active log already fsynced.
_LIVE: weakref.WeakSet[GraphStore] = weakref.WeakSet()


def _publish_on_exit() -> None:
    for store in list(_LIVE):
        try:
            store.close()
        except Exception:
            log.debug("graph-ted-db: exit publish failed", exc_info=True)


atexit.register(_publish_on_exit)

# Above this many nodes plus edges, open warns (docs/overview.md, Graph size).
LARGE_GRAPH_RECORDS = 250_000

# Windows attributes of a OneDrive Files-On-Demand (cloud-only) placeholder.
_CLOUD_ONLY_ATTRS = (
    getattr(stat_mod, "FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS", 0x400000)
    | getattr(stat_mod, "FILE_ATTRIBUTE_RECALL_ON_OPEN", 0x40000)
    | getattr(stat_mod, "FILE_ATTRIBUTE_OFFLINE", 0x1000)
)


def is_cloud_only(st: os.stat_result) -> bool:
    """True if a file is a cloud-only placeholder whose bytes are not on disk."""
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & _CLOUD_ONLY_ATTRS)


class SharedStoreError(RuntimeError):
    """The operation would rewrite files another device also writes."""


@dataclass
class DoctorReport:
    """Result of `GraphStore.doctor` / `graph-ted-db doctor`.

    ``dangling_edges_found`` lists live edges whose endpoints are not live
    nodes. They are only tombstoned (``dangling_edges_tombstoned``) when
    ``fix`` is true: in a synced folder an edge often arrives before its nodes,
    and a tombstone written then would delete it on every device.
    """

    torn_repaired: list[str] = field(default_factory=list)
    foreign_torn: list[str] = field(default_factory=list)
    cloud_only: list[str] = field(default_factory=list)
    empty_files: list[str] = field(default_factory=list)
    tmp_removed: list[str] = field(default_factory=list)
    labels_rebuilt: bool = False
    dangling_edges_found: list[str] = field(default_factory=list)
    dangling_edges_tombstoned: list[str] = field(default_factory=list)
    skipped_lines: list[str] = field(default_factory=list)
    conflict_copies: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    unregistered_writers: list[str] = field(default_factory=list)
    legacy_shared_files: list[str] = field(default_factory=list)
    legacy_v2_files: list[str] = field(default_factory=list)
    migrated: list[str] = field(default_factory=list)
    sealed_torn: list[str] = field(default_factory=list)
    meta_problems: list[str] = field(default_factory=list)
    fix: bool = False

    def summary(self) -> str:
        lines = [
            f"torn lines repaired: {len(self.torn_repaired)}",
            f"unterminated tails left in other devices' files: {len(self.foreign_torn)}",
            f"cloud-only placeholder files: {len(self.cloud_only)}",
            f"empty record files: {len(self.empty_files)}",
            f"tmp files removed: {len(self.tmp_removed)}",
            f"labels.json rebuilt: {self.labels_rebuilt}",
            f"dangling edges found: {len(self.dangling_edges_found)}",
            f"dangling edges tombstoned: {len(self.dangling_edges_tombstoned)}",
            f"skipped invalid lines: {len(self.skipped_lines)}",
            f"conflict copies: {len(self.conflict_copies)}",
            f"writers: {len(self.writers)}",
            f"writer files without a registration (still syncing?): "
            f"{len(self.unregistered_writers)}",
            f"v1 shared files (read only): {len(self.legacy_shared_files)}",
            f"v2 files not sealed yet: {len(self.legacy_v2_files)}",
            f"v2 files sealed into segments: {len(self.migrated)}",
            f"sealed segments with a torn tail (not modified): {len(self.sealed_torn)}",
            f"app meta files with problems (not modified): {len(self.meta_problems)}",
        ]
        for item in self.torn_repaired:
            lines.append(f"  repaired {item}")
        for item in self.foreign_torn:
            lines.append(f"  not modified (still syncing?) {item}")
        for item in self.cloud_only:
            lines.append(f"  cloud-only (set the folder to always keep on this device) {item}")
        for item in self.empty_files:
            lines.append(f"  empty (still syncing, or not downloaded?) {item}")
        verb = "tombstoned" if self.fix else "would tombstone"
        for item in self.dangling_edges_found:
            lines.append(f"  dangling ({verb}) {item}")
        for item in self.skipped_lines:
            lines.append(f"  skipped {item}")
        for item in self.conflict_copies:
            lines.append(f"  conflict {item}")
        for item in self.legacy_v2_files:
            lines.append(f"  legacy v2 (still mutable until its writer opens) {item}")
        for item in self.migrated:
            lines.append(f"  sealed {item}")
        for item in self.sealed_torn:
            lines.append(f"  sealed torn (not modified) {item}")
        for item in self.meta_problems:
            lines.append(f"  meta {item}")
        if self.dangling_edges_found and not self.fix:
            lines.append(
                "dangling edges were reported, not deleted. In a synced folder their nodes may"
                " still be on the way; once sync has finished, run doctor with --fix to tombstone them."
            )
        return "\n".join(lines) + "\n"


@dataclass
class _Txn:
    """In-memory write buffer for one local transaction (not synced)."""

    ops: list[tuple[str, Any]] = field(default_factory=list)


class GraphStore(_GraphStoreAliases):
    """Single-writer (this machine) view of a graph folder.

    Primary writes are ``put_node``, ``put_edge``, ``make_node``, and
    ``make_edge``. ``add_node``, ``add_edge``, ``nodes``, ``edges``,
    ``has_node``, ``has_edge``, ``neighbors``, ``remove_node``, and
    ``remove_edge`` are optional NetworkX-like aliases for those operations.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        data_dir: Path | None = None,
        publish: PublishPolicy | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.meta: GraphMeta = load_graph_meta(self.root)
        self.data_dir = data_dir
        self._policy = publish if publish is not None else PublishPolicy.from_env()
        # Under the device LOCK, so concurrent processes agree on one id.
        with exclusive_lock(lock_path_for(self.meta.id, data_dir)):
            self.writer_id = load_or_create_writer_id(writer_path_for(self.meta.id, data_dir))
        self.paths = GraphPaths(self.root, writer=self.writer_id)
        self._active_root = active_dir_for(self.meta.id, data_dir)
        self._stage_dir = stage_dir_for(self.meta.id, data_dir)
        self._active_since: dict[str, float] = {}
        self._active_last: dict[str, float] = {}
        self._stop = threading.Event()
        self._publisher: threading.Thread | None = None
        self._sealed_torn: list[str] = []
        self._legacy_v2: list[str] = []
        self._migrated: list[str] = []
        _LIVE.add(self)
        # Hybrid logical clock: the greatest (updated_at, counter) seen in the
        # store or written here. New writes always sort after it, so an edit
        # made after seeing a record wins even if this device's clock is behind.
        self._hlc: tuple[str, int] = ("", 0)
        self._registered = False
        self._lock_path = lock_path_for(self.meta.id, data_dir)
        self._index_dir = index_dir_for(self.meta.id, data_dir)
        self._index: LocalIndex | None = None
        self._index_fp: tuple[tuple[str, int, int], ...] | None = None
        self._wal_dir = wal_dir_for(self.meta.id, data_dir)
        self._foreign_torn: list[str] = []
        self._cloud_only: list[str] = []
        self._empty_files: list[str] = []
        self._tx: _Txn | None = None
        # Embeddings keyed by record id, then property name. Rebuilt when the
        # vector files change. Query views hold the inner dict, so a write in
        # the same statement is visible without a second scan.
        self._vector_map: dict[str, dict[str, tuple[float, ...]]] | None = None
        self._vector_fp: tuple[tuple[str, int, int], ...] | None = None
        self._lock_depth = 0
        # flock serializes other processes. It does not cover the
        # _lock_depth short-circuit: a second thread in this process saw a
        # non-zero depth and entered the critical section beside the holder.
        # The RLock makes that check and _tx per-thread re-entrant only.
        self._thread_lock = threading.RLock()
        self.skipped_lines: list[str] = []
        # While a transaction is being applied, label updates are held here
        # and written once. None means each caller writes labels immediately.
        self._label_acc: tuple[set[str], set[str], set[str]] | None = None
        # Paths touched while a multi-record commit is applied. The fingerprint
        # is updated from these after fsync, instead of scanning every shard.
        self._fp_pending: list[Path] | None = None

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        data_dir: Path | None = None,
        publish: PublishPolicy | None = None,
    ) -> GraphStore:
        """Open an existing graph folder.

        Repairs this device's local active log, seals any legacy files this
        writer still owns, replays an interrupted transaction, publishes active
        bytes a crash left behind, and builds the local index before returning.

        Args:
            root: The graph folder (created by `init_graph`).
            data_dir: Where to keep the process lock, the local index, and the
                active record log. Defaults to `GRAPH_TED_DB_DATA` or the
                platform app-data directory, never inside the graph folder.
            publish: When to seal the active log into immutable segment files.
                Defaults to `PublishPolicy.from_env()`.

        Returns:
            An open store.
        """
        started = time.perf_counter()
        store = cls(root, data_dir=data_dir, publish=publish)
        with store._lock():
            store._recover_files()
            store._recover_active()
            store._migrate_owned_legacy()
            store._replay_wal_unlocked()
            # Bytes left in the active log were already committed (or just
            # replayed). Seal them so a crash does not hide them from sync.
            store._publish_due_unlocked(force=True)
            store._rebuild_index_unlocked()
            n_records = 0
            if store._index is not None:
                n_records = len(store._index.nodes) + len(store._index.edges)
            if n_records > LARGE_GRAPH_RECORDS:
                log.warning(
                    "graph-ted-db: open took %.1fs for %d records; about 100,000 is comfortable."
                    " See Graph size in the docs (docs/overview.md)",
                    time.perf_counter() - started,
                    n_records,
                )
            problems = store.problems()
            if any(problems.values()):
                log.warning(
                    "graph-ted-db: %s opened with problems %s; run `graph-ted-db doctor`",
                    store.meta.name,
                    problems,
                )
        store._start_publisher()
        return store

    def problems(self) -> dict[str, int]:
        """Counts of things that may hide records. All zero on a healthy store.

        ``skipped_lines``: lines that are not valid records (including an
        unterminated last line). ``unterminated_other_writers``: other writers'
        files whose last line is incomplete, left unmodified (usually still
        syncing). ``cloud_only_files``: cloud-only placeholders (OneDrive Files
        On-Demand). ``empty_record_files``: zero-byte record files.
        """
        return {
            "skipped_lines": len(set(self.skipped_lines)),
            "unterminated_other_writers": len(self._foreign_torn),
            "cloud_only_files": len(self._cloud_only),
            "empty_record_files": len(self._empty_files),
            "meta_problems": len(self._meta_problems()),
            "legacy_v2_files": len(self._legacy_v2_paths()),
            "sealed_torn": len(self._sealed_torn),
        }

    @contextmanager
    def _lock(self):
        with self._thread_lock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            with exclusive_lock(self._lock_path):
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0

    # --- nodes ---

    def put_node(self, record: NodeRecord) -> NodeRecord:
        """Write a node record as-is (last write wins). Returns the record."""
        with self._lock():
            return self._put_node_unlocked(record)

    def _put_node_unlocked(self, record: NodeRecord) -> NodeRecord:
        record = self._stamp(record)
        if self._tx is not None:
            self._tx.ops.append(("put_node", record))
            if self._index is not None:
                self._index.upsert_node(record)
            return record
        self._disk_put_node(record)
        return record

    def _disk_put_node(self, record: NodeRecord) -> None:
        path = self.paths.node_shard(record.id)
        self._append(path, record.to_jsonl())
        self._remember_labels(node_labels=record.labels)
        if self._index is not None:
            self._index.upsert_node(record)

    def get_node(self, record_id: str) -> NodeRecord | None:
        """Return the live node with this id, or None if it is missing or deleted."""
        record_id = normalize_uuid(record_id)
        with self._lock():
            return self._get_node_unlocked(record_id)

    def delete_node(self, record_id: str, *, updated_by: str = "") -> Tombstone:
        """Tombstone a node and every live incident edge (DETACH DELETE)."""
        record_id = normalize_uuid(record_id)
        with self._lock():
            now = self._clock_now()
            return self._delete_node_unlocked(record_id, updated_by=updated_by, now=now)

    def _delete_node_unlocked(
        self,
        record_id: str,
        *,
        updated_by: str = "",
        now: str | None = None,
    ) -> Tombstone:
        stamp = now or self._clock_now()
        record_id = normalize_uuid(record_id)
        incident: list[str] = []
        if self._index is not None:
            incident = [entry.edge_id for entry in self._index.adj.get(record_id, ())]
        else:
            incident = [
                edge.id
                for edge in self._lww_edges_unlocked()
                if edge.from_id == record_id or edge.to_id == record_id
            ]
        for edge_id in incident:
            self._delete_edge_unlocked(edge_id, updated_by=updated_by, now=stamp)
        tomb = Tombstone(
            id=record_id,
            kind="node",
            updated_at=stamp,
            updated_by=updated_by,
        )
        tomb = self._stamp(tomb)
        if self._tx is not None:
            self._tx.ops.append(("delete_node", tomb))
            if self._index is not None:
                self._index.remove_node(record_id)
            return tomb
        self._disk_delete_tomb(tomb)
        if self._index is not None:
            self._index.remove_node(record_id)
        return tomb

    def iter_nodes(self) -> Iterator[NodeRecord]:
        """Yield every live node (the last-write-wins version of each id)."""
        with self._lock():
            out = self._lww_nodes_unlocked()
        yield from out

    # --- edges ---

    def put_edge(self, record: EdgeRecord) -> EdgeRecord:
        """Write an edge record as-is (last write wins). Returns the record.

        Raises:
            ValueError: If either endpoint is not a live node.
        """
        with self._lock():
            return self._put_edge_unlocked(record)

    def _put_edge_unlocked(self, record: EdgeRecord) -> EdgeRecord:
        if self._live_node_unlocked(record.from_id) is None:
            raise ValueError(f"edge from_id is not a live node: {record.from_id}")
        if self._live_node_unlocked(record.to_id) is None:
            raise ValueError(f"edge to_id is not a live node: {record.to_id}")
        record = self._stamp(record)
        if self._tx is not None:
            self._tx.ops.append(("put_edge", record))
            if self._index is not None:
                self._index.upsert_edge(record)
            return record
        self._disk_put_edge(record)
        return record

    def _disk_put_edge(self, record: EdgeRecord) -> None:
        path = self.paths.edge_shard(record.id)
        self._append(path, record.to_jsonl())
        self._remember_labels(relationship_types=(record.type,))
        if self._index is not None:
            self._index.upsert_edge(record)

    def get_edge(self, record_id: str) -> EdgeRecord | None:
        """Return the live edge with this id, or None if it is missing, deleted, or dangling."""
        record_id = normalize_uuid(record_id)
        with self._lock():
            return self._get_graph_edge_unlocked(record_id)

    def delete_edge(self, record_id: str, *, updated_by: str = "") -> Tombstone:
        """Tombstone one edge. Returns the tombstone that was written."""
        record_id = normalize_uuid(record_id)
        tomb = Tombstone(
            id=record_id,
            kind="edge",
            updated_at=self._clock_now(),
            updated_by=updated_by,
        )
        with self._lock():
            return self._delete_edge_unlocked(record_id, updated_by=updated_by, now=tomb.updated_at)

    def _delete_edge_unlocked(
        self,
        record_id: str,
        *,
        updated_by: str = "",
        now: str | None = None,
    ) -> Tombstone:
        record_id = normalize_uuid(record_id)
        tomb = Tombstone(
            id=record_id,
            kind="edge",
            updated_at=now or self._clock_now(),
            updated_by=updated_by,
        )
        tomb = self._stamp(tomb)
        if self._tx is not None:
            self._tx.ops.append(("delete_edge", tomb))
            if self._index is not None:
                self._index.remove_edge(record_id)
            return tomb
        self._disk_delete_tomb(tomb)
        if self._index is not None:
            self._index.remove_edge(record_id)
        return tomb

    def iter_edges(self) -> Iterator[EdgeRecord]:
        """Yield every live edge whose endpoints are both live nodes."""
        with self._lock():
            out = self._graph_edges_unlocked()
        yield from out

    # --- vectors ---

    def put_vector(self, record: VectorRecord) -> VectorRecord:
        """Write an embedding record (stored under `vectors/<property>/`). Returns the record."""
        with self._lock():
            return self._put_vector_unlocked(record)

    def _put_vector_unlocked(self, record: VectorRecord) -> VectorRecord:
        record = self._stamp(record)
        if self._tx is not None:
            self._tx.ops.append(("put_vector", record))
            self._cache_put_vector(record)
            return record
        self._disk_put_vector(record)
        return record

    def _disk_put_vector(self, record: VectorRecord) -> None:
        path = self.paths.vector_shard(record.property, record.id)
        self._append(path, record.to_jsonl())
        self._remember_labels(vector_properties=(record.property,))
        self._cache_put_vector(record)
        # The file changed. The next query reloads; views already holding a
        # bucket keep the value just cached.
        self._vector_fp = None

    def _cache_put_vector(self, record: VectorRecord) -> None:
        if self._vector_map is None:
            return
        bucket = self._vector_map.setdefault(record.id, {})
        bucket[record.property] = record.floats()

    def _cache_drop_vector(self, record_id: str, property_name: str) -> None:
        if self._vector_map is None:
            return
        bucket = self._vector_map.get(record_id)
        if bucket is not None:
            bucket.pop(property_name, None)

    def _ensure_vector_map_unlocked(self) -> None:
        """Load live embeddings if the vector files changed since last time.

        Call this before building query views. A map that is already current
        is left in place so those views keep sharing its buckets.
        """
        fp = self._vector_fingerprint()
        if self._vector_map is not None and fp == self._vector_fp:
            return
        loaded: dict[str, dict[str, tuple[float, ...]]] = {}
        for vec in self._lww_vectors_unlocked():
            loaded.setdefault(vec.id, {})[vec.property] = vec.floats()
        if self._tx is not None:
            for kind, payload in self._tx.ops:
                if kind == "put_vector":
                    loaded.setdefault(payload.id, {})[payload.property] = payload.floats()
                elif kind == "delete_vector" and payload.kind == "vector" and payload.property:
                    loaded.get(payload.id, {}).pop(payload.property, None)
        self._vector_map = loaded
        self._vector_fp = fp

    def _vector_bucket_unlocked(self, record_id: str) -> dict[str, tuple[float, ...]]:
        if self._vector_map is None:
            self._ensure_vector_map_unlocked()
        assert self._vector_map is not None
        bucket = self._vector_map.get(record_id)
        if bucket is None:
            bucket = {}
            self._vector_map[record_id] = bucket
        return bucket

    def _vector_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        """mtime+size of vector JSONL, including bundles and the active log."""
        items: list[tuple[str, int, int]] = []
        seen: set[str] = set()

        def add(path: Path) -> None:
            if not path.is_file():
                return
            try:
                st = path.stat()
            except OSError:
                return
            if st.st_size == 0:
                return
            key = self._fp_key(path)
            if key in seen:
                return
            seen.add(key)
            items.append((key, st.st_mtime_ns, st.st_size))

        for name in self._vector_property_names():
            directory = self.paths.vectors_dir / name
            if directory.is_dir():
                for child in directory.iterdir():
                    if child.is_file() and is_record_file_name(child.name):
                        add(child)
            for path in self._active_logs(directory):
                add(path)
        return tuple(sorted(items))

    def get_vector(self, property_name: str, record_id: str) -> VectorRecord | None:
        """Return the live embedding `property_name` for a node or edge id, or None.

        Raises:
            ValueError: If `property_name` is not a valid vector property name.
        """
        if not is_vector_property_name(property_name):
            raise ValueError(f"invalid vector property name: {property_name!r}")
        record_id = normalize_uuid(record_id)
        with self._lock():
            lives = self._load_vectors_for(property_name, record_id)
            tombs = self._tombstones("vector", record_id, property_name)
            picked = resolve(lives, tombs)
            if picked is None or picked.is_tombstone:
                return None
            assert isinstance(picked.payload, VectorRecord)
            return picked.payload

    def delete_vector(
        self, property_name: str, record_id: str, *, updated_by: str = ""
    ) -> Tombstone:
        """Tombstone the embedding `property_name` of a node or edge. Returns the tombstone."""
        if not is_vector_property_name(property_name):
            raise ValueError(f"invalid vector property name: {property_name!r}")
        record_id = normalize_uuid(record_id)
        tomb = Tombstone(
            id=record_id,
            kind="vector",
            property=property_name,
            updated_at=self._clock_now(),
            updated_by=updated_by,
        )
        with self._lock():
            tomb = self._stamp(replace(tomb, updated_at=self._clock_now()))
            self._append(self.paths.deleted_jsonl, tomb.to_jsonl())
            self._cache_drop_vector(record_id, property_name)
            self._vector_fp = None
        return tomb

    # --- helpers for callers ---

    def make_node(
        self,
        *,
        labels: list[str] | tuple[str, ...] = (),
        props: dict[str, Any] | None = None,
        record_id: str | None = None,
        updated_by: str = "",
    ) -> NodeRecord:
        """Create or overwrite a node and return it.

        Args:
            labels: Node labels, e.g. `["Person"]`.
            props: JSON-compatible property values.
            record_id: UUID to write. A new random UUID is used when omitted;
                an existing id is overwritten (last write wins).
            updated_by: Optional author string stored on the record.
        """
        rec = NodeRecord(
            id=normalize_uuid(record_id) if record_id else str(uuid4()),
            updated_at=self._clock_now(),
            labels=tuple(labels),
            props=dict(props or {}),
            updated_by=updated_by,
        )
        return self.put_node(rec)

    def make_edge(
        self,
        *,
        type: str,
        from_id: str,
        to_id: str,
        props: dict[str, Any] | None = None,
        record_id: str | None = None,
        updated_by: str = "",
    ) -> EdgeRecord:
        """Create or overwrite an edge and return it.

        Args:
            type: Relationship type, e.g. `"KNOWS"`.
            from_id: Id of the live start node.
            to_id: Id of the live end node.
            props: JSON-compatible property values.
            record_id: UUID to write. A new random UUID is used when omitted.
            updated_by: Optional author string stored on the record.

        Raises:
            ValueError: If either endpoint is not a live node.
        """
        rec = EdgeRecord(
            id=normalize_uuid(record_id) if record_id else str(uuid4()),
            updated_at=self._clock_now(),
            type=type,
            from_id=normalize_uuid(from_id),
            to_id=normalize_uuid(to_id),
            props=dict(props or {}),
            updated_by=updated_by,
        )
        return self.put_edge(rec)

    def execute(
        self,
        cypher: str,
        parameters: dict[str, Any] | None = None,
        *,
        updated_by: str = "cypher",
    ) -> list[dict[str, Any]]:
        """Run an openCypher query (the supported subset). See docs/cypher.md."""
        with self._lock():
            return self._execute_unlocked(cypher, parameters or {}, updated_by=updated_by)

    def execute_many(
        self,
        statements: list[tuple[str, dict[str, Any]]],
        *,
        updated_by: str = "cypher",
    ) -> list[list[dict[str, Any]]]:
        """Run statements in one lock and one transaction. All commit or none."""
        if not statements:
            return []
        with self._lock():
            if self._tx is not None:
                raise RuntimeError("execute_many cannot run inside an open transaction")
            self._refresh_index_unlocked()
            self._begin_unlocked()
            try:
                out: list[list[dict[str, Any]]] = []
                for cypher, params in statements:
                    out.append(
                        self._execute_unlocked(
                            str(cypher), dict(params or {}), updated_by=updated_by
                        )
                    )
                self._commit_unlocked()
                return out
            except Exception:
                self._rollback_unlocked()
                raise

    def _execute_unlocked(
        self,
        cypher: str,
        parameters: dict[str, Any],
        *,
        updated_by: str = "cypher",
    ) -> list[dict[str, Any]]:
        from graph_ted_db.engine import execute_cypher

        started = self._tx is None
        if started:
            self._refresh_index_unlocked()
        started = self._begin_unlocked()
        try:
            rows = execute_cypher(self, cypher, parameters, updated_by=updated_by)
            if started:
                self._commit_unlocked()
            return rows
        except Exception:
            if started:
                self._rollback_unlocked()
            raise

    def _begin_unlocked(self) -> bool:
        if self._tx is not None:
            return False
        self._tx = _Txn()
        return True

    def _commit_unlocked(self) -> None:
        if self._tx is None:
            return
        ops = list(self._tx.ops)
        self._tx = None
        if not ops:
            return
        # One record needs no separate commit file. The fsynced line is the
        # whole transaction; a torn tail is truncated and is not a record.
        if len(ops) == 1:
            self._apply_ops_unlocked(ops)
            return
        # Several records: fsync one commit record before any shard byte,
        # append the lines, fsync each touched file once, then drop the record.
        # The record lives in one reused file so later commits do not create
        # a new directory entry.
        crash_if("before-wal")
        wal = self._publish_wal(ops)
        crash_if("after-wal")
        self._apply_ops_durable(ops)
        crash_if("before-unlink")
        self._clear_wal(wal)

    def _rollback_unlocked(self) -> None:
        self._tx = None
        self._vector_map = None
        self._vector_fp = None
        self._rebuild_index_unlocked()

    def _apply_ops_durable(self, ops: list[tuple[str, Any]]) -> None:
        """Append every op, then flush and fsync each file once."""
        self._label_acc = (set(), set(), set())
        self._fp_pending = []
        try:
            with append_batch():
                for index, op in enumerate(ops):
                    self._apply_ops_unlocked([op])
                    if index == 0 and len(ops) > 1:
                        crash_if("mid-apply")
                labels, types, vecs = self._label_acc
                self._label_acc = None
                if labels or types or vecs:
                    self._note_labels(
                        node_labels=labels,
                        relationship_types=types,
                        vector_properties=vecs,
                    )
            self._note_pending_files()
        finally:
            self._label_acc = None
            self._fp_pending = None
        self._publish_due_unlocked(force=False)

    def _apply_ops_unlocked(self, ops: list[tuple[str, Any]]) -> None:
        for kind, payload in ops:
            if kind == "put_node":
                self._disk_put_node(payload)
            elif kind == "put_edge":
                self._disk_put_edge(payload)
            elif kind == "put_vector":
                self._disk_put_vector(payload)
            elif kind in ("delete_node", "delete_edge", "delete_vector"):
                self._disk_delete_tomb(payload)
                if self._index is not None:
                    if kind == "delete_node":
                        self._index.remove_node(payload.id)
                    elif kind == "delete_edge":
                        self._index.remove_edge(payload.id)

    def _wal_file(self) -> Path:
        return self._wal_dir / "current.jsonl"

    def _publish_wal(self, ops: list[tuple[str, Any]]) -> Path:
        """Append one checksummed commit record and fsync that file.

        ``wal-fsync`` dies before the bytes are installed, so a kill there
        leaves the transaction absent. A torn tail has no checksum and is
        not a commit.
        """
        self._wal_dir.mkdir(parents=True, exist_ok=True)
        path = self._wal_file()
        created = not path.exists()
        if os.environ.get("GRAPH_TED_DB_CRASH_AT") == "wal-fsync":
            os.kill(os.getpid(), signal.SIGKILL)
        lines = [_wal_encode(op) for op in ops]
        lines.append(json.dumps({"op": "commit", "n": len(ops)}, separators=(",", ":")))
        payload = "".join(line.rstrip("\n") + "\n" for line in lines).encode("utf-8")
        with path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            fsync_directory(path.parent)
        return path

    def _clear_wal(self, path: Path) -> None:
        path.write_bytes(b"")

    def _replay_wal_unlocked(self) -> None:
        if not self._wal_dir.is_dir():
            return
        for path in sorted(self._wal_dir.glob("*.jsonl")):
            # A torn tail is not a commit. Only a checksummed group is applied,
            # so a crash keeps or drops each transaction as a whole.
            repair_torn_jsonl(path)
            groups = _wal_groups(path)
            if groups:
                for ops in groups:
                    self._apply_ops_durable(ops)
            try:
                path.unlink()
            except OSError:
                pass

    def _disk_delete_tomb(self, tomb: Tombstone) -> None:
        self._append(self.paths.deleted_jsonl, tomb.to_jsonl())

    def publish(self) -> int:
        """Seal every non-empty active log into the synced folder now.

        Returns how many directories were sealed. Each one becomes a single
        new file. Safe to call more than once. Other devices still wait for
        their sync client to copy the new files.
        """
        with self._lock():
            return self._publish_due_unlocked(force=True)

    def close(self) -> None:
        """Seal active logs and stop the background publisher."""
        self._stop.set()
        try:
            self.publish()
        except Exception:
            log.debug("graph-ted-db: close could not publish", exc_info=True)

    def __del__(self) -> None:
        stop = getattr(self, "_stop", None)
        if stop is None or stop.is_set():
            return
        try:
            self.close()
        except Exception:
            return

    def compact(self) -> None:
        """Merge this writer's sealed segments down to winning records.

        Only files whose name carries this writer id are rewritten, and only
        by publishing a new segment and deleting the old ones. Another
        writer's files are not modified. Refused while a sync client has left
        conflict copies: deleting a forked file can drop records.

        Raises:
            SharedStoreError: Conflict copies are present.
        """
        with self._lock():
            if self._conflict_copy_paths():
                raise SharedStoreError(
                    "compact is disabled while sync conflict copies are present;"
                    " leave them until the sync client is idle"
                )
            self._publish_due_unlocked(force=True)
            self._compact_nodes()
            self._compact_edges()
            self._compact_vectors()
            self._compact_tombstones()
            self._rebuild_index_unlocked()

    def doctor(self, *, fix: bool = False) -> DoctorReport:
        """Repair torn JSONL, drop leftover tmp, rebuild labels, report dangling edges.

        Dangling edges (an endpoint is not a live node) are only reported
        unless ``fix`` is true. Reads already skip them. Tombstoning is
        permanent and syncs to every device, and in a shared folder an edge
        can arrive before its nodes, so run with ``fix=True`` only once the
        sync client has finished.
        """
        report = DoctorReport(fix=fix)
        with self._lock():
            report.torn_repaired, report.tmp_removed = self._recover_files()
            report.torn_repaired.extend(self._recover_active())
            self._migrate_owned_legacy()
            report.foreign_torn = list(self._foreign_torn)
            report.sealed_torn = list(self._sealed_torn)
            report.cloud_only = list(self._cloud_only)
            report.empty_files = list(self._empty_files)
            dangling = self._dangling_edges_unlocked()
            report.dangling_edges_found = [
                f"{edge.id} type={edge.type} from={edge.from_id} to={edge.to_id}"
                for edge in dangling
            ]
            if dangling and fix:
                now = self._clock_now()
                for edge in dangling:
                    self._delete_edge_unlocked(edge.id, updated_by="doctor", now=now)
                report.dangling_edges_tombstoned = list(report.dangling_edges_found)
                # Tombstones have to be in the synced folder before doctor
                # returns. They are permanent and every other device must see
                # them; leaving them in the local active log would hide the fix.
                self._publish_due_unlocked(force=True)
            report.migrated = list(self._migrated)
            self._rebuild_labels_unlocked()
            self._rebuild_index_unlocked()
            report.labels_rebuilt = True
            report.skipped_lines = list(self.skipped_lines)
            report.conflict_copies = [str(p) for p in self._conflict_copy_paths()]
            report.writers = self.writers()
            seen: set[str] = set()
            legacy: list[str] = []
            for path in self._managed_files():
                if not path.name.endswith(".jsonl"):
                    continue
                owner = writer_of(path.name)
                if owner is not None:
                    seen.add(owner)
                elif path.stat().st_size > 0 and str(path) not in report.conflict_copies:
                    legacy.append(str(path))
            report.unregistered_writers = sorted(seen - set(report.writers))
            report.legacy_shared_files = legacy
            report.legacy_v2_files = [str(p) for p in self._legacy_v2_paths()]
            report.meta_problems = self._meta_problems()
        return report

    # --- app metadata files (meta/<namespace>/<name>.<writer>.json) ---

    def meta_put(
        self, directory: str, name: str, data: dict[str, Any], *, replace_existing: bool = False
    ) -> Path:
        """Write ``meta/<directory>/<name>.<this-writer>.json`` atomically.

        Only this writer's own file is ever written, so devices that publish
        at the same time each keep their file. An existing file is not
        replaced unless ``replace_existing`` (use that for pointers, not for
        append-only versions).

        Raises:
            FileExistsError: the file exists and ``replace_existing`` is false.
            MetaPathError: unsafe directory or name, or a store-reserved one.
        """
        if not isinstance(data, dict):
            raise TypeError("meta data must be a JSON object")
        path = meta_path(self.paths.meta_dir, directory, name, self.writer_id)
        with self._lock():
            if path.exists() and not replace_existing:
                raise FileExistsError(str(path))
            if not self._registered:
                self._register_writer_unlocked()
                self._registered = True
            replace_json_file(path, data)
        return path

    def meta_list(self, directory: str) -> list[MetaEntry]:
        """Every file in ``meta/<directory>/`` from all writers, sorted by name.

        Unreadable files are returned with ``problem`` set (and no data), never
        silently skipped and never modified. Subdirectories are not listed.
        """
        segs = check_segments(directory)
        base = self.paths.meta_dir.joinpath(*segs)
        if not base.is_dir():
            return []
        out = []
        for child in sorted(base.iterdir()):
            if child.is_file():
                out.append(read_entry(child, cloud_only=is_cloud_only(child.stat())))
        return out

    def _meta_problems(self) -> list[str]:
        found = []
        for directory in app_meta_dirs(self.paths.meta_dir):
            for child in sorted(directory.iterdir()):
                if not child.is_file():
                    continue
                entry = read_entry(child, cloud_only=is_cloud_only(child.stat()))
                if entry.problem:
                    found.append(f"{entry.problem} {self._rel(child)}")
        return found

    def export(self, out: str | Path) -> dict[str, int]:
        """Write the current state (live winners only) to one JSONL file.

        The first line is a header; then one ``{"type": ..., "record": ...}``
        line per live node, edge and vector. History, tombstones and conflict
        copies are not included. Read it back with `import_export`.

        Returns:
            Counts per record type.
        """
        out = Path(out).expanduser().resolve()
        if out.is_relative_to(self.root):
            raise ValueError("write the export outside the graph folder")
        counts = {"nodes": 0, "edges": 0, "vectors": 0}
        with self._lock():
            lines = [
                json.dumps(
                    {
                        "export": FORMAT_NAME,
                        "format_version": FORMAT_VERSION,
                        "name": self.meta.name,
                        "exported_at": format_timestamp(),
                    }
                )
            ]
            for node in self._lww_nodes_unlocked():
                lines.append(json.dumps({"type": "node", "record": node.to_dict()}))
                counts["nodes"] += 1
            for edge in self._graph_edges_unlocked():
                lines.append(json.dumps({"type": "edge", "record": edge.to_dict()}))
                counts["edges"] += 1
            for vec in self._lww_vectors_unlocked():
                rec = {"property": vec.property, **vec.to_dict()}
                lines.append(json.dumps({"type": "vector", "record": rec}))
                counts["vectors"] += 1
        replace_jsonl(out, lines)
        return counts

    def _vector_property_names(self) -> list[str]:
        """Property names with a synced directory or a local active log."""
        names: set[str] = set()
        for root in (self.paths.vectors_dir, self._active_root / "vectors"):
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_dir() and is_vector_property_name(child.name):
                    names.add(child.name)
        return sorted(names)

    def _lww_vectors_unlocked(self) -> list[VectorRecord]:
        out: list[VectorRecord] = []
        names = self._vector_property_names()
        if not names:
            return out
        live = {n.id for n in self._lww_nodes_unlocked()} | {
            e.id for e in self._graph_edges_unlocked()
        }
        for name in names:
            prop_dir = self.paths.vectors_dir / name
            tombs: dict[str, list[Tombstone]] = defaultdict(list)
            for tomb in self._iter_tombstones():
                if tomb.kind == "vector" and tomb.property == prop_dir.name:
                    tombs[tomb.id].append(tomb)
            grouped: dict[str, list[VectorRecord]] = defaultdict(list)
            for stem in self._stems(prop_dir):
                for path in self._stem_files(prop_dir, stem):
                    for rec in self._parse_vectors(path, prop_dir.name):
                        grouped[rec.id].append(rec)
            for path in self._shared_sources(prop_dir):
                for rec in self._parse_vectors(path, prop_dir.name):
                    grouped[rec.id].append(rec)
            for record_id, lives in grouped.items():
                picked = resolve(lives, tombs.get(record_id, []))
                if picked is not None and not picked.is_tombstone and record_id in live:
                    assert isinstance(picked.payload, VectorRecord)
                    out.append(picked.payload)
        return out

    def refresh_index(self) -> None:
        """Reload the query catalog if another process wrote shards."""
        with self._lock():
            self._refresh_index_unlocked()

    def _rebuild_index_unlocked(self) -> None:
        self._index = LocalIndex(self._index_dir)
        self._index.rebuild(self._lww_nodes_unlocked(), self._graph_edges_unlocked())
        self._index.save()
        self._note_index_current()

    def _refresh_index_unlocked(self) -> None:
        fingerprint = self._shard_fingerprint()
        if self._index is None or fingerprint != self._index_fp:
            self._rebuild_index_unlocked()

    def _touch_index(self, path: Path | None = None) -> None:
        # A multi-record transaction notes the fingerprint once, after fsync.
        if path is not None and self._is_vector_path(path):
            return
        if self._label_acc is not None:
            if path is not None and self._fp_pending is not None:
                self._fp_pending.append(path)
            return
        if path is not None and self._index_fp is not None:
            self._note_one_file(path)
            return
        self._note_index_current()

    def _is_vector_path(self, path: Path) -> bool:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.paths.vectors_dir.resolve())
            return True
        except ValueError:
            pass
        try:
            rel = resolved.relative_to(self._active_root.resolve())
        except ValueError:
            return False
        return bool(rel.parts) and rel.parts[0] == "vectors"

    def _note_one_file(self, path: Path) -> None:
        assert self._index_fp is not None
        try:
            stat = path.stat()
        except OSError:
            self._note_index_current()
            return
        rel = self._fp_key(path)
        kept = [item for item in self._index_fp if item[0] != rel]
        kept.append((rel, stat.st_mtime_ns, stat.st_size))
        self._index_fp = tuple(sorted(kept))

    def _note_pending_files(self) -> None:
        pending = self._fp_pending or []
        if self._index_fp is None:
            self._note_index_current()
            return
        seen: set[Path] = set()
        for path in pending:
            if path in seen:
                continue
            seen.add(path)
            self._note_one_file(path)

    def _note_index_current(self) -> None:
        self._index_fp = self._shard_fingerprint()

    def _shard_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        """mtime+size of node/edge/tombstone JSONL (source of truth for MATCH)."""
        items: list[tuple[str, int, int]] = []
        for directory in (
            self.paths.nodes_dir,
            self.paths.edges_dir,
            self.paths.meta_dir,
        ):
            if not directory.is_dir():
                continue
            prefix = directory.name
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    name = entry.name
                    if not is_record_file_name(name):
                        continue
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    items.append((f"{prefix}/{name}", stat.st_mtime_ns, stat.st_size))
        active = self._active_root
        if active.is_dir():
            for path in active.rglob("*.jsonl"):
                if not path.is_file():
                    continue
                try:
                    if self._is_vector_path(path):
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                items.append((self._fp_key(path), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(items))

    def _fp_key(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            pass
        try:
            rel = resolved.relative_to(self._active_root.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()
        return f"active/{rel}"

    # --- internals ---

    # --- ownership (which files this device writes) ---

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _owns(self, path: Path) -> bool:
        return writer_of(path.name) == self.writer_id

    def _append(self, path: Path, line: str) -> None:
        # The synced path is only a name. Bytes go to one local log per directory,
        # never one file per shard: a later seal publishes a single bundle.
        if not self._registered:
            self._register_writer_unlocked()
            self._registered = True
        active = self._active_log(path.parent)
        first = not active.is_file() or active.stat().st_size == 0
        append_jsonl(active, line, repair=True)
        now = time.monotonic()
        key = self._fp_key(active)
        if first or key not in self._active_since:
            self._active_since[key] = now
        self._active_last[key] = now
        self._touch_index(active)
        if not in_append_batch():
            self._maybe_publish_directory(path.parent, force=False)

    def _active_dir(self, directory: Path) -> Path:
        try:
            rel = directory.resolve().relative_to(self.root.resolve())
        except ValueError:
            rel = Path(directory.name)
        return self._active_root.joinpath(*rel.parts)

    def _active_log(self, directory: Path) -> Path:
        """One growing log for every shard in `directory`. Not inside the synced folder."""
        return self._active_dir(directory) / "records.jsonl"

    def _active_file(self, directory: Path, stem: str) -> Path:
        """Local log a test or a previous process may still have for one stem.

        New writes go to ``_active_log``. This name is only how leftover
        per-shard logs are found.
        """
        return self._active_dir(directory) / f"{stem}.jsonl"

    def _stem_files(self, directory: Path, stem: str) -> list[Path]:
        """v1, v2, per-shard segments, their conflict copies, and a leftover per-shard log."""
        found = shard_jsonl_files(directory, stem)
        leftover = self._active_file(directory, stem)
        if leftover.is_file() and leftover.stat().st_size > 0:
            found.append(leftover)
        return found

    def _shared_sources(self, directory: Path) -> list[Path]:
        """Bundles and the single active log. Callers read these once, not once per shard."""
        found = bundle_jsonl_files(directory)
        active = self._active_log(directory)
        if active.is_file() and active.stat().st_size > 0:
            found.append(active)
        return found

    def _active_logs(self, directory: Path) -> list[Path]:
        active_dir = self._active_dir(directory)
        if not active_dir.is_dir():
            return []
        found: list[Path] = []
        for child in sorted(active_dir.glob("*.jsonl")):
            if child.is_file() and child.stat().st_size > 0:
                found.append(child)
        return found

    def _stems(self, directory: Path) -> list[str]:
        stems = set(discover_shard_stems(directory))
        active_dir = self._active_dir(directory)
        if active_dir.is_dir():
            for child in active_dir.iterdir():
                if not child.is_file() or not child.name.endswith(".jsonl"):
                    continue
                stem = child.name[: -len(".jsonl")]
                if stem == "deleted" or (
                    len(stem) == 2 and all(c in "0123456789abcdef" for c in stem)
                ):
                    stems.add(stem)
        return sorted(stems)

    def _own_paths(self, directory: Path, stem: str) -> list[Path]:
        """This writer's per-shard files for one stem, plus a leftover per-shard log."""
        found = [path for path in shard_jsonl_files(directory, stem) if self._owns(path)]
        leftover = self._active_file(directory, stem)
        if leftover.is_file() and leftover.stat().st_size > 0:
            found.append(leftover)
        return found

    def _legacy_v2_paths(self) -> list[Path]:
        found: list[Path] = []
        for path in self._managed_files():
            if path.is_file() and is_legacy_v2_name(path.name):
                found.append(path)
        return found

    def _start_publisher(self) -> None:
        period = self._publisher_period()
        if period is None or self._publisher is not None:
            return
        ref = weakref.ref(self)
        stop = self._stop

        def loop() -> None:
            while not stop.wait(period):
                store = ref()
                if store is None:
                    return
                try:
                    store.publish_due()
                except Exception:
                    log.debug("graph-ted-db: background publish failed", exc_info=True)
                finally:
                    del store

        thread = threading.Thread(target=loop, name="gtdb-publish", daemon=True)
        self._publisher = thread
        thread.start()

    def _publisher_period(self) -> float | None:
        waits = [
            value
            for value in (self._policy.idle, self._policy.max_interval)
            if value > 0
        ]
        if not waits:
            return None
        return max(0.05, min(0.25, min(waits) / 2))

    def publish_due(self) -> None:
        """Seal active logs that have hit the size, interval, or idle rule."""
        if self._stop.is_set():
            return
        with self._lock():
            self._publish_due_unlocked(force=False)

    def _publish_due_unlocked(self, *, force: bool) -> int:
        if not self._active_root.is_dir():
            return 0
        sealed = 0
        seen: set[Path] = set()
        for path in sorted(self._active_root.rglob("*.jsonl")):
            if not path.is_file():
                continue
            directory, _stem = self._synced_target(path)
            if directory in seen:
                continue
            seen.add(directory)
            if self._maybe_publish_directory(directory, force=force):
                sealed += 1
        return sealed

    def _maybe_publish_directory(self, directory: Path, *, force: bool) -> bool:
        """Seal every shard in `directory` into one file when the publish rule says so."""
        if in_append_batch():
            return False
        logs = self._active_logs(directory)
        if not logs:
            return False
        now = time.monotonic()
        total = 0
        oldest = now
        newest = 0.0
        for path in logs:
            try:
                total += path.stat().st_size
            except OSError:
                continue
            key = self._fp_key(path)
            oldest = min(oldest, self._active_since.get(key, now))
            newest = max(newest, self._active_last.get(key, now))
        quiet = now - newest if newest else 0.0
        if not due(self._policy, size=total, age=now - oldest, quiet=quiet, force=force):
            return False
        return self._seal_directory(directory, logs) is not None

    def _seal_directory(self, directory: Path, logs: list[Path]) -> Path | None:
        """Publish one immutable file for every non-empty active log in `directory`."""
        parts: list[bytes] = []
        for path in logs:
            if has_unterminated_tail(path):
                repair_torn_jsonl(path)
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if not data:
                continue
            parts.append(data if data.endswith(b"\n") else data + b"\n")
        payload = b"".join(parts)
        if not payload:
            return None
        tombstones = directory == self.paths.meta_dir
        if self._payload_already_sealed(directory, payload, tombstones=tombstones):
            self._truncate_logs(logs)
            return None
        if not self._registered:
            self._register_writer_unlocked()
            self._registered = True
        directory.mkdir(parents=True, exist_ok=True)
        if tombstones:
            dest = segment_path(
                directory,
                "deleted",
                self.writer_id,
                next_seq(directory, "deleted", self.writer_id),
            )
        else:
            dest = bundle_path(
                directory, self.writer_id, next_bundle_seq(directory, self.writer_id)
            )
        publish_immutable(dest, payload, stage_dir=self._stage_dir)
        self._truncate_logs(logs)
        self._touch_index(dest)
        self._maybe_autocompact(directory)
        return dest

    def _truncate_logs(self, logs: list[Path]) -> None:
        for path in logs:
            truncate_file(path)
            key = self._fp_key(path)
            self._active_since.pop(key, None)
            self._active_last.pop(key, None)
            self._touch_index(path)

    def _payload_already_sealed(self, directory: Path, data: bytes, *, tombstones: bool) -> bool:
        paths = (
            self._own_sealed(directory, "deleted") if tombstones else self._own_bundles(directory)
        )
        for path in paths:
            try:
                if path.stat().st_size == len(data) and path.read_bytes() == data:
                    return True
            except OSError:
                continue
        return False

    def _synced_target(self, active: Path) -> tuple[Path, str]:
        rel = active.resolve().relative_to(self._active_root.resolve())
        stem = rel.name[: -len(".jsonl")]
        parent = self.root.joinpath(*rel.parts[:-1])
        return parent, stem

    def _own_sealed(self, directory: Path, stem: str) -> list[Path]:
        found: list[Path] = []
        if not directory.is_dir():
            return found
        prefix = f"{stem}.{self.writer_id}."
        for child in sorted(directory.iterdir()):
            sealed = child.is_file() and is_sealed_segment_name(child.name)
            if (
                sealed
                and child.name.startswith(prefix)
                and segment_stem(child.name) == stem
                and writer_of(child.name) == self.writer_id
            ):
                found.append(child)
        return found

    def _own_bundles(self, directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        found: list[Path] = []
        for child in sorted(directory.iterdir()):
            if (
                child.is_file()
                and is_bundle_name(child.name)
                and writer_of(child.name) == self.writer_id
            ):
                found.append(child)
        return found

    def _maybe_autocompact(self, directory: Path) -> None:
        limit = self._policy.compact_after
        if limit <= 0:
            return
        if directory == self.paths.meta_dir:
            if len(self._own_sealed(directory, "deleted")) > limit:
                self._compact_tombstones()
            return
        if len(self._own_bundles(directory)) <= limit:
            return
        if self._is_vector_path(directory):
            self._compact_vectors(directory.name)
        elif directory == self.paths.nodes_dir:
            self._compact_nodes()
        elif directory == self.paths.edges_dir:
            self._compact_edges()

    def _install_payload(self, directory: Path, lines: list[str], *, kind: str) -> None:
        """Publish one new file of `lines` and delete this writer's older files.

        ``kind`` is ``bundle`` (every shard) or ``deleted`` (tombstones). No
        existing file is modified. An empty result deletes our old files and
        does not leave a zero-byte file in the synced folder.
        """
        payload = "".join(line.rstrip("\n") + "\n" for line in lines).encode("utf-8")
        existing = self._superseded(directory, kind)
        sealed = [path for path in existing if is_sealed_segment_name(path.name)]
        legacy = [path for path in existing if is_legacy_v2_name(path.name)]
        active = self._active_logs(directory)
        if len(sealed) == 1 and not legacy and not active and sealed[0].read_bytes() == payload:
            return
        dest: Path | None = None
        if payload:
            directory.mkdir(parents=True, exist_ok=True)
            if kind == "deleted":
                dest = segment_path(
                    directory,
                    "deleted",
                    self.writer_id,
                    next_seq(directory, "deleted", self.writer_id),
                )
            else:
                dest = bundle_path(
                    directory, self.writer_id, next_bundle_seq(directory, self.writer_id)
                )
            publish_immutable(dest, payload, stage_dir=self._stage_dir)
            self._touch_index(dest)
        for path in sealed + legacy:
            if dest is not None and path.resolve() == dest.resolve():
                continue
            try:
                path.unlink()
            except OSError:
                pass
            self._touch_index(path)
        self._truncate_logs(active)
        if directory.is_dir():
            fsync_directory(directory)

    def _superseded(self, directory: Path, kind: str) -> list[Path]:
        """This writer's files in `directory` that a rewrite replaces."""
        if not directory.is_dir():
            return []
        found: list[Path] = []
        for child in sorted(directory.iterdir()):
            if not child.is_file() or writer_of(child.name) != self.writer_id:
                continue
            stem = segment_stem(child.name)
            bundle_or_shard = is_bundle_name(child.name) or is_per_shard_segment_name(child.name)
            legacy_shard = is_legacy_v2_name(child.name) and stem != "deleted"
            if (kind == "deleted" and stem == "deleted") or (
                kind != "deleted" and (bundle_or_shard or legacy_shard)
            ):
                found.append(child)
        return found

    def _recover_active(self) -> list[str]:
        torn: list[str] = []
        if not self._active_root.is_dir():
            return torn
        for path in sorted(self._active_root.rglob("*.jsonl")):
            discarded = repair_torn_jsonl(path)
            if discarded:
                torn.append(f"{path} ({discarded} bytes)")
        if self._stage_dir.is_dir():
            for child in self._stage_dir.iterdir():
                if child.is_file():
                    try:
                        child.unlink()
                    except OSError:
                        pass
        return torn

    def _fold_directories(self) -> list[Path]:
        found = [self.paths.nodes_dir, self.paths.edges_dir, self.paths.meta_dir]
        for name in self._vector_property_names():
            found.append(self.paths.vectors_dir / name)
        return found

    def _migrate_owned_legacy(self) -> list[str]:
        """Fold this writer's v2 files and per-shard segments into one bundle.

        The bytes are copied as they are. Last-write-wins still runs at read
        time, so a dangling edge or an older version is not dropped here:
        ``compact()`` is what rewrites winners. Other writers' files are left
        for them. A reader that still sees both the old names and the bundle
        unions them. The old names are removed only after the bundle is durable.
        """
        moved: list[str] = []
        for directory in self._fold_directories():
            if not directory.is_dir():
                continue
            old = [
                path
                for path in sorted(directory.iterdir())
                if path.is_file()
                and writer_of(path.name) == self.writer_id
                and (is_legacy_v2_name(path.name) or is_per_shard_segment_name(path.name))
            ]
            if not old:
                continue
            if not self._registered:
                self._register_writer_unlocked()
                self._registered = True
            moved.extend(self._rel(path) for path in old)
            self._fold_owned_files(directory, old)
        self._migrated.extend(moved)
        self._legacy_v2 = [str(p) for p in self._legacy_v2_paths()]
        return moved

    def _fold_owned_files(self, directory: Path, old: list[Path]) -> None:
        parts: list[bytes] = []
        for path in old:
            if not is_sealed_segment_name(path.name) and has_unterminated_tail(path):
                repair_torn_jsonl(path)
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if data:
                parts.append(data if data.endswith(b"\n") else data + b"\n")
        payload = b"".join(parts)
        tombstones = directory == self.paths.meta_dir
        if payload and not self._payload_already_sealed(
            directory, payload, tombstones=tombstones
        ):
            if tombstones:
                dest = segment_path(
                    directory,
                    "deleted",
                    self.writer_id,
                    next_seq(directory, "deleted", self.writer_id),
                )
            else:
                dest = bundle_path(
                    directory, self.writer_id, next_bundle_seq(directory, self.writer_id)
                )
            publish_immutable(dest, payload, stage_dir=self._stage_dir)
            self._touch_index(dest)
        for path in old:
            try:
                path.unlink()
            except OSError:
                pass
            self._touch_index(path)
        if directory.is_dir():
            fsync_directory(directory)

    def _register_writer_unlocked(self) -> None:
        """Announce this writer in meta/writers/ and mark the store format v4.

        Each writer only ever creates its own registration file, so the
        writers list never conflicts in a sync client.
        """
        reg = self.paths.writers_dir / f"{self.writer_id}.json"
        if not reg.is_file():
            self.paths.writers_dir.mkdir(parents=True, exist_ok=True)
            replace_json_file(
                reg,
                {
                    "writer": self.writer_id,
                    "format_version": FORMAT_VERSION,
                    "created_at": format_timestamp(),
                },
            )
        if self.meta.format_version < FORMAT_VERSION:
            extras = dict(self.meta.extras)
            extras["layout"] = LAYOUT_BUNDLES
            self.meta = replace(self.meta, format_version=FORMAT_VERSION, extras=extras)
            replace_json_file(self.paths.graph_json, self.meta.to_dict())

    def writers(self) -> list[str]:
        """Writer ids registered in meta/writers/ (synced from every device)."""
        found: set[str] = set()
        if self.paths.writers_dir.is_dir():
            for child in self.paths.writers_dir.iterdir():
                stem = child.name.removesuffix(".json")
                if child.name.endswith(".json") and is_writer_id(stem):
                    found.add(stem)
        return sorted(found)

    # --- hybrid logical clock ---

    def _observe(self, updated_at: str, counter: int) -> None:
        # format_timestamp output is fixed-width, so strings sort like times.
        if (updated_at, counter) > self._hlc:  # noqa: PLR1730 (hot path, keep explicit)
            self._hlc = (updated_at, counter)

    def _clock_now(self) -> str:
        """Wall time for a new version: never earlier than anything seen."""
        now = format_timestamp()
        return max(now, self._hlc[0])

    def _stamp(self, record: Any) -> Any:
        """Give a version this writer's id and the next HLC counter."""
        wall, counter = self._hlc
        if record.updated_at == wall:
            counter += 1
        elif record.updated_at > wall:
            counter = 0
        else:
            # An explicit older timestamp (put_* as-is): keep it, it loses LWW.
            return replace(record, writer=self.writer_id, counter=0)
        self._hlc = (record.updated_at, counter)
        return replace(record, writer=self.writer_id, counter=counter)

    def _foreign_record_files(self) -> list[Path]:
        return [
            p
            for p in self._managed_files()
            if p.suffix.lower() == ".jsonl" and p.stat().st_size > 0 and not self._owns(p)
        ]

    def _recover_files(self) -> tuple[list[str], list[str]]:
        torn: list[str] = []
        tmp_removed: list[str] = []
        self._foreign_torn = []
        self._sealed_torn = []
        self._cloud_only = []
        self._empty_files = []
        for path in self._managed_files():
            if path.name.endswith(".tmp"):
                try:
                    path.unlink()
                    tmp_removed.append(str(path))
                except OSError:
                    pass
                continue
            if path.suffix.lower() == ".jsonl":
                try:
                    st = path.stat()
                except OSError:
                    continue
                if is_cloud_only(st):
                    # Reading would ask the sync client to download it; if
                    # that fails the records are missing. Report it.
                    self._cloud_only.append(str(path))
                elif st.st_size == 0:
                    self._empty_files.append(str(path))
                    continue
                if is_sealed_segment_name(path.name):
                    # Published bytes are immutable, including our own. A missing
                    # newline is a partial delivery, not a tail we may truncate.
                    if has_unterminated_tail(path):
                        self._sealed_torn.append(str(path))
                        if not self._owns(path):
                            self._foreign_torn.append(str(path))
                    continue
                if not self._owns(path):
                    # Another device's file, or a v1 shared file: a missing
                    # newline usually means the sync client has not finished.
                    if has_unterminated_tail(path):
                        self._foreign_torn.append(str(path))
                    continue
                # Owned legacy v2 file. Repair, then migration seals it.
                discarded = repair_torn_jsonl(path)
                if discarded:
                    torn.append(f"{path} ({discarded} bytes)")
        self._legacy_v2 = [str(p) for p in self._legacy_v2_paths()]
        return torn, tmp_removed

    def _managed_files(self) -> list[Path]:
        found: list[Path] = []
        directories = [
            self.paths.nodes_dir,
            self.paths.edges_dir,
            self.paths.meta_dir,
        ]
        if self.paths.vectors_dir.is_dir():
            for child in sorted(self.paths.vectors_dir.iterdir()):
                if child.is_dir():
                    directories.append(child)
        for directory in directories:
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if not child.is_file():
                    continue
                name = child.name
                if name.endswith((".tmp", ".jsonl")):
                    found.append(child)
        found.extend(sorted(self.root.glob("graph.json*.tmp")))
        return found

    def _conflict_copy_paths(self) -> list[Path]:
        # The canonical name may be absent (rclone bisync renames both sides),
        # so filter by name rather than dropping the first file. A bundle does
        # not start with a shard stem, so its conflict copies are listed apart
        # from the per-stem scan.
        def copies(directory: Path, stem: str) -> list[Path]:
            return [
                p
                for p in shard_jsonl_files(directory, stem)
                if p.name != f"{stem}.jsonl" and writer_of(p.name) is None
            ]

        def bundle_copies(directory: Path) -> list[Path]:
            return [p for p in bundle_jsonl_files(directory) if writer_of(p.name) is None]

        found: list[Path] = []
        for directory in (self.paths.nodes_dir, self.paths.edges_dir):
            if directory.is_dir():
                found.extend(bundle_copies(directory))
            for stem in discover_shard_stems(directory):
                found.extend(copies(directory, stem))
        if self.paths.vectors_dir.is_dir():
            for prop_dir in sorted(self.paths.vectors_dir.iterdir()):
                if not prop_dir.is_dir():
                    continue
                found.extend(bundle_copies(prop_dir))
                for stem in discover_shard_stems(prop_dir):
                    found.extend(copies(prop_dir, stem))
        if self.paths.meta_dir.is_dir():
            found.extend(bundle_copies(self.paths.meta_dir))
        found.extend(copies(self.paths.meta_dir, "deleted"))
        return found

    def _get_node_unlocked(self, record_id: str) -> NodeRecord | None:
        lives = self._load_nodes_for(record_id)
        tombs = self._tombstones("node", record_id)
        picked = resolve(lives, tombs)
        if picked is None or picked.is_tombstone:
            return None
        assert isinstance(picked.payload, NodeRecord)
        return picked.payload

    def _live_node_unlocked(self, record_id: str) -> NodeRecord | None:
        """Index hit, else disk LWW.

        A node created earlier in this transaction is in the index and not yet
        on disk. Returning that hit skips a directory listing. A miss still
        reads the shard, so a stale index cannot hide a live disk node.
        """
        try:
            record_id = normalize_uuid(record_id)
        except ValueError:
            return None
        index = self._index
        if index is not None:
            rec = index.nodes.get(record_id)
            if rec is not None:
                return rec
            mapped = index.uuid_to_id.get(record_id)
            if mapped is not None:
                rec = index.nodes.get(mapped)
                if rec is not None:
                    return rec
        return self._get_node_unlocked(record_id)

    def _get_graph_edge_unlocked(self, record_id: str) -> EdgeRecord | None:
        lives = self._load_edges_for(record_id)
        tombs = self._tombstones("edge", record_id)
        picked = resolve(lives, tombs)
        if picked is None or picked.is_tombstone:
            return None
        assert isinstance(picked.payload, EdgeRecord)
        edge = picked.payload
        if self._get_node_unlocked(edge.from_id) is None:
            return None
        if self._get_node_unlocked(edge.to_id) is None:
            return None
        return edge

    def _lww_nodes_unlocked(self) -> list[NodeRecord]:
        out: list[NodeRecord] = []
        tombs_by_id = self._tombstones_by_id("node")
        grouped: dict[str, list[NodeRecord]] = defaultdict(list)
        for stem in self._stems(self.paths.nodes_dir):
            for path in self._stem_files(self.paths.nodes_dir, stem):
                for rec in self._parse_nodes(path):
                    grouped[rec.id].append(rec)
        for path in self._shared_sources(self.paths.nodes_dir):
            for rec in self._parse_nodes(path):
                grouped[rec.id].append(rec)
        for record_id, lives in grouped.items():
            picked = resolve(lives, tombs_by_id.get(record_id, []))
            if picked is not None and not picked.is_tombstone:
                assert isinstance(picked.payload, NodeRecord)
                out.append(picked.payload)
        return out

    def _lww_edges_unlocked(self) -> list[EdgeRecord]:
        out: list[EdgeRecord] = []
        tombs_by_id = self._tombstones_by_id("edge")
        grouped: dict[str, list[EdgeRecord]] = defaultdict(list)
        for stem in self._stems(self.paths.edges_dir):
            for path in self._stem_files(self.paths.edges_dir, stem):
                for rec in self._parse_edges(path):
                    grouped[rec.id].append(rec)
        for path in self._shared_sources(self.paths.edges_dir):
            for rec in self._parse_edges(path):
                grouped[rec.id].append(rec)
        for record_id, lives in grouped.items():
            picked = resolve(lives, tombs_by_id.get(record_id, []))
            if picked is not None and not picked.is_tombstone:
                assert isinstance(picked.payload, EdgeRecord)
                out.append(picked.payload)
        return out

    def _graph_edges_unlocked(self) -> list[EdgeRecord]:
        live_nodes = {node.id for node in self._lww_nodes_unlocked()}
        return [
            edge
            for edge in self._lww_edges_unlocked()
            if edge.from_id in live_nodes and edge.to_id in live_nodes
        ]

    def _dangling_edges_unlocked(self) -> list[EdgeRecord]:
        live_nodes = {node.id for node in self._lww_nodes_unlocked()}
        return [
            edge
            for edge in self._lww_edges_unlocked()
            if edge.from_id not in live_nodes or edge.to_id not in live_nodes
        ]

    def _load_nodes_for(self, record_id: str) -> list[NodeRecord]:
        return self._load_matching(self.paths.nodes_dir, record_id, self._parse_nodes)

    def _load_edges_for(self, record_id: str) -> list[EdgeRecord]:
        return self._load_matching(self.paths.edges_dir, record_id, self._parse_edges)

    def _load_vectors_for(self, property_name: str, record_id: str) -> list[VectorRecord]:
        directory = self.paths.vectors_dir / property_name

        def parse(path: Path) -> Iterator[VectorRecord]:
            return self._parse_vectors(path, property_name)

        return self._load_matching(directory, record_id, parse)

    def _load_matching(self, directory: Path, record_id: str, parse: Any) -> list[Any]:
        stem = shard_id(record_id)
        out: list[Any] = []
        paths = self._stem_files(directory, stem) + self._shared_sources(directory)
        for path in paths:
            for rec in parse(path):
                if rec.id == record_id:
                    out.append(rec)
        return out

    def _parse_nodes(self, path: Path) -> Iterator[NodeRecord]:
        for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
            try:
                rec = NodeRecord.from_dict(obj)
            except ValueError:
                self.skipped_lines.append(f"{path}:{lineno}")
                continue
            self._observe(rec.updated_at, rec.counter)
            yield rec

    def _parse_edges(self, path: Path) -> Iterator[EdgeRecord]:
        for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
            try:
                edge = EdgeRecord.from_dict(obj)
            except ValueError:
                self.skipped_lines.append(f"{path}:{lineno}")
                continue
            self._observe(edge.updated_at, edge.counter)
            yield edge

    def _parse_vectors(self, path: Path, property_name: str) -> Iterator[VectorRecord]:
        for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
            try:
                vec = VectorRecord.from_dict(obj, property=property_name)
            except ValueError:
                self.skipped_lines.append(f"{path}:{lineno}")
                continue
            self._observe(vec.updated_at, vec.counter)
            yield vec

    def _tombstone_files(self) -> list[Path]:
        found = self._stem_files(self.paths.meta_dir, "deleted")
        for path in self._shared_sources(self.paths.meta_dir):
            if path not in found:
                found.append(path)
        return found

    def _iter_tombstones(self) -> Iterator[Tombstone]:
        for path in self._tombstone_files():
            for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
                try:
                    tomb = Tombstone.from_dict(obj)
                except ValueError:
                    self.skipped_lines.append(f"{path}:{lineno}")
                    continue
                self._observe(tomb.updated_at, tomb.counter)
                yield tomb

    def _tombstones(
        self, kind: str, record_id: str, property_name: str | None = None
    ) -> list[Tombstone]:
        out: list[Tombstone] = []
        for tomb in self._iter_tombstones():
            if tomb.kind != kind or tomb.id != record_id:
                continue
            if kind == "vector" and tomb.property != property_name:
                continue
            out.append(tomb)
        return out

    def _tombstones_by_id(self, kind: str) -> dict[str, list[Tombstone]]:
        grouped: dict[str, list[Tombstone]] = defaultdict(list)
        for tomb in self._iter_tombstones():
            if tomb.kind == kind:
                grouped[tomb.id].append(tomb)
        return grouped

    def _remember_labels(
        self,
        *,
        node_labels: tuple[str, ...] | list[str] = (),
        relationship_types: tuple[str, ...] | list[str] = (),
        vector_properties: tuple[str, ...] | list[str] = (),
    ) -> None:
        acc = self._label_acc
        if acc is None:
            self._note_labels(
                node_labels=node_labels,
                relationship_types=relationship_types,
                vector_properties=vector_properties,
            )
            return
        acc[0].update(node_labels)
        acc[1].update(relationship_types)
        acc[2].update(vector_properties)

    def _note_labels(
        self,
        *,
        node_labels: Iterable[str] = (),
        relationship_types: Iterable[str] = (),
        vector_properties: Iterable[str] = (),
    ) -> None:
        path = self.paths.labels_json
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except json.JSONDecodeError:
            current = {}
        if not isinstance(current, dict):
            current = dict(EMPTY_LABELS)
        labels = set(current.get("node_labels") or [])
        types = set(current.get("relationship_types") or [])
        vecs = set(current.get("vector_properties") or [])
        labels.update(node_labels)
        types.update(relationship_types)
        vecs.update(vector_properties)
        payload = {
            "node_labels": sorted(labels),
            "relationship_types": sorted(types),
            "vector_properties": sorted(vecs),
        }
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                current = None
            if (
                isinstance(current, dict)
                and sorted(current.get("node_labels") or []) == payload["node_labels"]
                and sorted(current.get("relationship_types") or []) == payload["relationship_types"]
                and sorted(current.get("vector_properties") or []) == payload["vector_properties"]
            ):
                return
        replace_json_file(path, payload)

    def _rebuild_labels_unlocked(self) -> None:
        labels: set[str] = set()
        types: set[str] = set()
        vecs: set[str] = set()
        for node in self._lww_nodes_unlocked():
            labels.update(node.labels)
        for edge in self._lww_edges_unlocked():
            types.add(edge.type)
        vecs.update(self._vector_property_names())
        replace_json_file(
            self.paths.labels_json,
            {
                "node_labels": sorted(labels),
                "relationship_types": sorted(types),
                "vector_properties": sorted(vecs),
            },
        )

    def _sources(self, directory: Path, *, own: bool) -> list[Path]:
        found: list[Path] = []
        if directory.is_dir():
            for child in sorted(directory.iterdir()):
                if not child.is_file() or not is_record_file_name(child.name):
                    continue
                if (writer_of(child.name) == self.writer_id) is not own:
                    continue
                found.append(child)
        if own:
            found.extend(self._active_logs(directory))
        return found

    def _compact_nodes(self) -> None:
        directory = self.paths.nodes_dir
        own_paths = self._sources(directory, own=True)
        if not own_paths:
            return
        tombs_by_id = self._tombstones_by_id("node")
        grouped: dict[str, list[NodeRecord]] = defaultdict(list)
        for path in own_paths:
            for rec in self._parse_nodes(path):
                grouped[rec.id].append(rec)
        others: dict[str, list[NodeRecord]] = defaultdict(list)
        for path in self._sources(directory, own=False):
            for rec in self._parse_nodes(path):
                others[rec.id].append(rec)
        lines: list[str] = []
        for record_id, lives in grouped.items():
            picked = resolve(lives + others.get(record_id, []), tombs_by_id.get(record_id, []))
            if picked is None or picked.is_tombstone:
                continue
            assert isinstance(picked.payload, NodeRecord)
            if picked.payload.writer in (self.writer_id, ""):
                lines.append(picked.payload.to_jsonl())
        self._install_payload(directory, lines, kind="bundle")

    def _compact_edges(self) -> None:
        directory = self.paths.edges_dir
        own_paths = self._sources(directory, own=True)
        if not own_paths:
            return
        tombs_by_id = self._tombstones_by_id("edge")
        live_nodes = {node.id for node in self._lww_nodes_unlocked()}
        grouped: dict[str, list[EdgeRecord]] = defaultdict(list)
        for path in own_paths:
            for rec in self._parse_edges(path):
                grouped[rec.id].append(rec)
        others: dict[str, list[EdgeRecord]] = defaultdict(list)
        for path in self._sources(directory, own=False):
            for rec in self._parse_edges(path):
                others[rec.id].append(rec)
        lines: list[str] = []
        for record_id, lives in grouped.items():
            picked = resolve(lives + others.get(record_id, []), tombs_by_id.get(record_id, []))
            if picked is None or picked.is_tombstone:
                continue
            assert isinstance(picked.payload, EdgeRecord)
            edge = picked.payload
            if edge.writer not in (self.writer_id, ""):
                continue
            if edge.from_id in live_nodes and edge.to_id in live_nodes:
                lines.append(edge.to_jsonl())
        self._install_payload(directory, lines, kind="bundle")

    def _compact_vectors(self, only: str | None = None) -> None:
        names = [only] if only is not None else self._vector_property_names()
        for name in names:
            prop_dir = self.paths.vectors_dir / name
            own_paths = self._sources(prop_dir, own=True)
            if not own_paths:
                continue
            tombs_by_id: dict[str, list[Tombstone]] = defaultdict(list)
            for tomb in self._iter_tombstones():
                if tomb.kind == "vector" and tomb.property == name:
                    tombs_by_id[tomb.id].append(tomb)
            grouped: dict[str, list[VectorRecord]] = defaultdict(list)
            for path in own_paths:
                for rec in self._parse_vectors(path, name):
                    grouped[rec.id].append(rec)
            others: dict[str, list[VectorRecord]] = defaultdict(list)
            for path in self._sources(prop_dir, own=False):
                for rec in self._parse_vectors(path, name):
                    others[rec.id].append(rec)
            lines: list[str] = []
            for record_id, lives in grouped.items():
                picked = resolve(lives + others.get(record_id, []), tombs_by_id.get(record_id, []))
                if picked is None or picked.is_tombstone:
                    continue
                assert isinstance(picked.payload, VectorRecord)
                if picked.payload.writer in (self.writer_id, ""):
                    lines.append(picked.payload.to_jsonl())
            self._install_payload(prop_dir, lines, kind="bundle")

    def _compact_tombstones(self) -> None:
        directory = self.paths.meta_dir
        own = [tomb for tomb in self._iter_tombstones() if tomb.writer == self.writer_id]
        if not own and not self._sources(directory, own=True):
            return
        best: dict[tuple[str, str, str | None], Tombstone] = {}
        for tomb in own:
            key = (tomb.kind, tomb.id, tomb.property)
            prev = best.get(key)
            if prev is None:
                best[key] = tomb
                continue
            picked = resolve([], [prev, tomb])
            if picked is not None and isinstance(picked.payload, Tombstone):
                best[key] = picked.payload
        lines = [tomb.to_jsonl() for tomb in best.values()]
        self._install_payload(directory, lines, kind="deleted")


def _wal_groups(path: Path) -> list[list[tuple[str, Any]]]:
    """Complete commit groups. A trailing group with no checksum is dropped."""
    groups: list[list[tuple[str, Any]]] = []
    buf: list[tuple[str, Any]] = []
    for _, obj in iter_json_objects(path):
        if obj.get("op") == "commit":
            if obj.get("n") == len(buf):
                groups.append(buf)
            buf = []
            continue
        decoded = _wal_decode(obj)
        if decoded is None:
            buf = []
            continue
        buf.append(decoded)
    return groups


def _wal_encode(op: tuple[str, Any]) -> str:
    kind, payload = op
    if kind.startswith(("put_", "delete_")):
        data = payload.to_dict()
        if kind == "put_vector":
            data["property"] = payload.property
        return json.dumps({"op": kind, "record": data}, separators=(",", ":"))
    return json.dumps({"op": kind}, separators=(",", ":"))


def _wal_decode(obj: dict[str, Any]) -> tuple[str, Any] | None:
    kind = obj.get("op")
    rec = obj.get("record")
    if not isinstance(kind, str) or not isinstance(rec, dict):
        return None
    if kind == "put_node":
        return kind, NodeRecord.from_dict(rec)
    if kind == "put_edge":
        return kind, EdgeRecord.from_dict(rec)
    if kind == "put_vector":
        prop = rec.get("property", "name_embedding")
        return kind, VectorRecord.from_dict(rec, property=str(prop))
    if kind in ("delete_node", "delete_edge", "delete_vector"):
        return kind, Tombstone.from_dict(rec)
    return None


def import_export(
    export_file: str | Path,
    dest: str | Path,
    *,
    name: str | None = None,
    data_dir: Path | None = None,
) -> GraphStore:
    """Create a new graph at `dest` from a file written by `GraphStore.export`.

    The new graph gets a new id. Records keep their ids and timestamps.
    Refuses to write into an existing graph.
    """
    from graph_ted_db.store.init import init_graph

    rows = list(iter_json_objects(Path(export_file)))
    if not rows or rows[0][1].get("export") != FORMAT_NAME:
        raise ValueError(f"not a graph-ted-db export: {export_file}")
    header = rows[0][1]
    init_graph(dest, name=name or str(header.get("name") or "graph"))
    store = GraphStore.open(dest, data_dir=data_dir)
    nodes, edges, vectors = [], [], []
    for _, obj in rows[1:]:
        rec, kind = obj.get("record"), obj.get("type")
        if not isinstance(rec, dict):
            raise ValueError(f"bad export line: {obj!r}")
        if kind == "node":
            nodes.append(NodeRecord.from_dict(rec))
        elif kind == "edge":
            edges.append(EdgeRecord.from_dict(rec))
        elif kind == "vector":
            vectors.append(VectorRecord.from_dict(rec, property=str(rec.get("property"))))
        else:
            raise ValueError(f"unknown export record type: {kind!r}")
    with store._lock():
        with append_batch():
            for node in nodes:
                store._put_node_unlocked(_unstamped(node))
            for edge in edges:
                store._put_edge_unlocked(_unstamped(edge))
            for vec in vectors:
                store._put_vector_unlocked(_unstamped(vec))
        store._rebuild_labels_unlocked()
        store._rebuild_index_unlocked()
    return store


def _unstamped(record: Any) -> Any:
    # The importing writer re-stamps every version as its own.
    return replace(record, writer="", counter=0)
