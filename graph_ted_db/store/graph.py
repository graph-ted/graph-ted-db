"""Read/write a graph folder: shards, tombstones, conflict-copy union, LWW."""

from __future__ import annotations

import json
import os
import signal
import threading
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from graph_ted_db.index.local import LocalIndex
from graph_ted_db.store.aliases import _GraphStoreAliases
from graph_ted_db.store.format import (
    format_timestamp,
    is_vector_property_name,
    normalize_uuid,
    shard_id,
)
from graph_ted_db.store.init import load_graph_meta
from graph_ted_db.store.jsonl import (
    append_batch,
    append_jsonl,
    crash_if,
    fsync_directory,
    iter_json_objects,
    repair_torn_jsonl,
    replace_json_file,
    replace_jsonl,
)
from graph_ted_db.store.lock import (
    exclusive_lock,
    index_dir_for,
    lock_path_for,
    wal_dir_for,
)
from graph_ted_db.store.lww import resolve
from graph_ted_db.store.paths import (
    GraphPaths,
    discover_shard_stems,
    is_record_file_name,
    shard_jsonl_files,
)
from graph_ted_db.store.records import (
    EMPTY_LABELS,
    EdgeRecord,
    GraphMeta,
    NodeRecord,
    Tombstone,
    VectorRecord,
)


@dataclass
class DoctorReport:
    """Result of `GraphStore.doctor` / `graph-ted-db doctor`.

    ``dangling_edges_found`` lists live edges whose endpoints are not live
    nodes. They are only tombstoned (``dangling_edges_tombstoned``) when
    ``fix`` is true: in a synced folder an edge often arrives before its nodes,
    and a tombstone written then would delete it on every device.
    """

    torn_repaired: list[str] = field(default_factory=list)
    tmp_removed: list[str] = field(default_factory=list)
    labels_rebuilt: bool = False
    dangling_edges_found: list[str] = field(default_factory=list)
    dangling_edges_tombstoned: list[str] = field(default_factory=list)
    skipped_lines: list[str] = field(default_factory=list)
    conflict_copies: list[str] = field(default_factory=list)
    fix: bool = False

    def summary(self) -> str:
        lines = [
            f"torn lines repaired: {len(self.torn_repaired)}",
            f"tmp files removed: {len(self.tmp_removed)}",
            f"labels.json rebuilt: {self.labels_rebuilt}",
            f"dangling edges found: {len(self.dangling_edges_found)}",
            f"dangling edges tombstoned: {len(self.dangling_edges_tombstoned)}",
            f"skipped invalid lines: {len(self.skipped_lines)}",
            f"conflict copies: {len(self.conflict_copies)}",
        ]
        for item in self.torn_repaired:
            lines.append(f"  repaired {item}")
        verb = "tombstoned" if self.fix else "would tombstone"
        for item in self.dangling_edges_found:
            lines.append(f"  dangling ({verb}) {item}")
        for item in self.skipped_lines:
            lines.append(f"  skipped {item}")
        for item in self.conflict_copies:
            lines.append(f"  conflict {item}")
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

    def __init__(self, root: str | Path, *, data_dir: Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.paths = GraphPaths(self.root)
        self.meta: GraphMeta = load_graph_meta(self.root)
        self.data_dir = data_dir
        self._lock_path = lock_path_for(self.meta.id, data_dir)
        self._index_dir = index_dir_for(self.meta.id, data_dir)
        self._index: LocalIndex | None = None
        self._index_fp: tuple[tuple[str, int, int], ...] | None = None
        self._wal_dir = wal_dir_for(self.meta.id, data_dir)
        self._tx: _Txn | None = None
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
    def open(cls, root: str | Path, *, data_dir: Path | None = None) -> GraphStore:
        """Open an existing graph folder.

        Repairs torn lines, replays an interrupted transaction, and builds the
        local index before returning.

        Args:
            root: The graph folder (created by `init_graph`).
            data_dir: Where to keep the process lock and local index. Defaults
                to `GRAPH_TED_DB_DATA` or the platform app-data directory,
                never inside the graph folder.

        Returns:
            An open store.
        """
        store = cls(root, data_dir=data_dir)
        with store._lock():
            store._recover_files()
            store._replay_wal_unlocked()
            store._rebuild_index_unlocked()
        return store

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
            self._put_node_unlocked(record)
        return record

    def _put_node_unlocked(self, record: NodeRecord) -> None:
        if self._tx is not None:
            self._tx.ops.append(("put_node", record))
            if self._index is not None:
                self._index.upsert_node(record)
            return
        self._disk_put_node(record)

    def _disk_put_node(self, record: NodeRecord) -> None:
        path = self.paths.node_shard(record.id)
        append_jsonl(path, record.to_jsonl())
        self._remember_labels(node_labels=record.labels)
        if self._index is not None:
            self._index.upsert_node(record)
        self._touch_index(path)

    def get_node(self, record_id: str) -> NodeRecord | None:
        """Return the live node with this id, or None if it is missing or deleted."""
        record_id = normalize_uuid(record_id)
        with self._lock():
            return self._get_node_unlocked(record_id)

    def delete_node(self, record_id: str, *, updated_by: str = "") -> Tombstone:
        """Tombstone a node and every live incident edge (DETACH DELETE)."""
        record_id = normalize_uuid(record_id)
        now = format_timestamp()
        tomb = Tombstone(
            id=record_id,
            kind="node",
            updated_at=now,
            updated_by=updated_by,
        )
        with self._lock():
            self._delete_node_unlocked(record_id, updated_by=updated_by, now=now)
        return tomb

    def _delete_node_unlocked(
        self,
        record_id: str,
        *,
        updated_by: str = "",
        now: str | None = None,
    ) -> Tombstone:
        stamp = now or format_timestamp()
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
        if self._tx is not None:
            self._tx.ops.append(("delete_node", tomb))
            if self._index is not None:
                self._index.remove_node(record_id)
            return tomb
        self._disk_delete_tomb(tomb)
        if self._index is not None:
            self._index.remove_node(record_id)
        self._touch_index(self.paths.deleted_jsonl)
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
            self._put_edge_unlocked(record)
        return record

    def _put_edge_unlocked(self, record: EdgeRecord) -> None:
        if self._live_node_unlocked(record.from_id) is None:
            raise ValueError(f"edge from_id is not a live node: {record.from_id}")
        if self._live_node_unlocked(record.to_id) is None:
            raise ValueError(f"edge to_id is not a live node: {record.to_id}")
        if self._tx is not None:
            self._tx.ops.append(("put_edge", record))
            if self._index is not None:
                self._index.upsert_edge(record)
            return
        self._disk_put_edge(record)

    def _disk_put_edge(self, record: EdgeRecord) -> None:
        path = self.paths.edge_shard(record.id)
        append_jsonl(path, record.to_jsonl())
        self._remember_labels(relationship_types=(record.type,))
        if self._index is not None:
            self._index.upsert_edge(record)
        self._touch_index(path)

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
            updated_at=format_timestamp(),
            updated_by=updated_by,
        )
        with self._lock():
            self._delete_edge_unlocked(record_id, updated_by=updated_by, now=tomb.updated_at)
        return tomb

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
            updated_at=now or format_timestamp(),
            updated_by=updated_by,
        )
        if self._tx is not None:
            self._tx.ops.append(("delete_edge", tomb))
            if self._index is not None:
                self._index.remove_edge(record_id)
            return tomb
        self._disk_delete_tomb(tomb)
        if self._index is not None:
            self._index.remove_edge(record_id)
        self._touch_index(self.paths.deleted_jsonl)
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
            self._put_vector_unlocked(record)
        return record

    def _put_vector_unlocked(self, record: VectorRecord) -> None:
        if self._tx is not None:
            self._tx.ops.append(("put_vector", record))
            return
        self._disk_put_vector(record)

    def _disk_put_vector(self, record: VectorRecord) -> None:
        path = self.paths.vector_shard(record.property, record.id)
        append_jsonl(path, record.to_jsonl())
        self._remember_labels(vector_properties=(record.property,))
        self._touch_index(path)

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
            updated_at=format_timestamp(),
            updated_by=updated_by,
        )
        with self._lock():
            append_jsonl(self.paths.deleted_jsonl, tomb.to_jsonl())
            self._touch_index(self.paths.deleted_jsonl)
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
            updated_at=format_timestamp(),
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
            updated_at=format_timestamp(),
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
        append_jsonl(self.paths.deleted_jsonl, tomb.to_jsonl())
        self._touch_index(self.paths.deleted_jsonl)

    def compact(self) -> None:
        """Rewrite canonical shards to winning records. Leaves conflict copies."""
        with self._lock():
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
            dangling = self._dangling_edges_unlocked()
            report.dangling_edges_found = [
                f"{edge.id} type={edge.type} from={edge.from_id} to={edge.to_id}"
                for edge in dangling
            ]
            if dangling and fix:
                now = format_timestamp()
                for edge in dangling:
                    self._delete_edge_unlocked(edge.id, updated_by="doctor", now=now)
                report.dangling_edges_tombstoned = list(report.dangling_edges_found)
            self._rebuild_labels_unlocked()
            self._rebuild_index_unlocked()
            report.labels_rebuilt = True
            report.skipped_lines = list(self.skipped_lines)
            report.conflict_copies = [str(p) for p in self._conflict_copy_paths()]
        return report

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
        if self._label_acc is not None:
            if path is not None and self._fp_pending is not None:
                try:
                    path.relative_to(self.paths.vectors_dir)
                except ValueError:
                    self._fp_pending.append(path)
            return
        if path is not None:
            try:
                path.relative_to(self.paths.vectors_dir)
                return
            except ValueError:
                pass
            if self._index_fp is not None:
                self._note_one_file(path)
                return
        self._note_index_current()

    def _note_one_file(self, path: Path) -> None:
        assert self._index_fp is not None
        try:
            stat = path.stat()
        except OSError:
            self._note_index_current()
            return
        # Same key shape as _shard_fingerprint ("nodes/00.jsonl"), also on Windows.
        rel = path.relative_to(self.root).as_posix()
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
        return tuple(sorted(items))

    # --- internals ---

    def _recover_files(self) -> tuple[list[str], list[str]]:
        torn: list[str] = []
        tmp_removed: list[str] = []
        for path in self._managed_files():
            if path.name.endswith(".tmp"):
                try:
                    path.unlink()
                    tmp_removed.append(str(path))
                except OSError:
                    pass
                continue
            if path.suffix.lower() == ".jsonl":
                discarded = repair_torn_jsonl(path)
                if discarded:
                    torn.append(f"{path} ({discarded} bytes)")
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
        graph_tmp = self.paths.graph_json.with_name(self.paths.graph_json.name + ".tmp")
        if graph_tmp.is_file():
            found.append(graph_tmp)
        return found

    def _conflict_copy_paths(self) -> list[Path]:
        # The canonical shard may be absent (rclone bisync renames both sides),
        # so filter by name rather than dropping the first file.
        def copies(directory: Path, stem: str) -> list[Path]:
            return [p for p in shard_jsonl_files(directory, stem) if p.name != f"{stem}.jsonl"]

        found: list[Path] = []
        for directory in (self.paths.nodes_dir, self.paths.edges_dir):
            for stem in discover_shard_stems(directory):
                found.extend(copies(directory, stem))
        if self.paths.vectors_dir.is_dir():
            for prop_dir in sorted(self.paths.vectors_dir.iterdir()):
                if not prop_dir.is_dir():
                    continue
                for stem in discover_shard_stems(prop_dir):
                    found.extend(copies(prop_dir, stem))
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
        for stem in discover_shard_stems(self.paths.nodes_dir):
            grouped: dict[str, list[NodeRecord]] = defaultdict(list)
            for rec in self._iter_node_records(stem):
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
        for stem in discover_shard_stems(self.paths.edges_dir):
            grouped: dict[str, list[EdgeRecord]] = defaultdict(list)
            for rec in self._iter_edge_records(stem):
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

    def _iter_node_records(self, stem: str) -> Iterator[NodeRecord]:
        for path in shard_jsonl_files(self.paths.nodes_dir, stem):
            yield from self._parse_nodes(path)

    def _iter_edge_records(self, stem: str) -> Iterator[EdgeRecord]:
        for path in shard_jsonl_files(self.paths.edges_dir, stem):
            yield from self._parse_edges(path)

    def _load_nodes_for(self, record_id: str) -> list[NodeRecord]:
        stem = shard_id(record_id)
        return [r for r in self._iter_node_records(stem) if r.id == record_id]

    def _load_edges_for(self, record_id: str) -> list[EdgeRecord]:
        stem = shard_id(record_id)
        return [r for r in self._iter_edge_records(stem) if r.id == record_id]

    def _load_vectors_for(self, property_name: str, record_id: str) -> list[VectorRecord]:
        stem = shard_id(record_id)
        directory = self.paths.vectors_dir / property_name
        out: list[VectorRecord] = []
        for path in shard_jsonl_files(directory, stem):
            for rec in self._parse_vectors(path, property_name):
                if rec.id == record_id:
                    out.append(rec)
        return out

    def _parse_nodes(self, path: Path) -> Iterator[NodeRecord]:
        for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
            try:
                yield NodeRecord.from_dict(obj)
            except ValueError:
                self.skipped_lines.append(f"{path}:{lineno}")

    def _parse_edges(self, path: Path) -> Iterator[EdgeRecord]:
        for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
            try:
                yield EdgeRecord.from_dict(obj)
            except ValueError:
                self.skipped_lines.append(f"{path}:{lineno}")

    def _parse_vectors(self, path: Path, property_name: str) -> Iterator[VectorRecord]:
        for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
            try:
                yield VectorRecord.from_dict(obj, property=property_name)
            except ValueError:
                self.skipped_lines.append(f"{path}:{lineno}")

    def _tombstone_files(self) -> list[Path]:
        return shard_jsonl_files(self.paths.meta_dir, "deleted")

    def _iter_tombstones(self) -> Iterator[Tombstone]:
        for path in self._tombstone_files():
            for lineno, obj in iter_json_objects(path, skipped=self.skipped_lines):
                try:
                    yield Tombstone.from_dict(obj)
                except ValueError:
                    self.skipped_lines.append(f"{path}:{lineno}")

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
        if self.paths.vectors_dir.is_dir():
            for prop_dir in self.paths.vectors_dir.iterdir():
                if prop_dir.is_dir() and is_vector_property_name(prop_dir.name):
                    vecs.add(prop_dir.name)
        replace_json_file(
            self.paths.labels_json,
            {
                "node_labels": sorted(labels),
                "relationship_types": sorted(types),
                "vector_properties": sorted(vecs),
            },
        )

    def _compact_nodes(self) -> None:
        tombs_by_id = self._tombstones_by_id("node")
        for stem in discover_shard_stems(self.paths.nodes_dir):
            grouped: dict[str, list[NodeRecord]] = defaultdict(list)
            for rec in self._iter_node_records(stem):
                grouped[rec.id].append(rec)
            lines: list[str] = []
            for record_id, lives in grouped.items():
                picked = resolve(lives, tombs_by_id.get(record_id, []))
                if picked is not None and not picked.is_tombstone:
                    lines.append(picked.payload.to_jsonl())
            replace_jsonl(self.paths.nodes_dir / f"{stem}.jsonl", lines)

    def _compact_edges(self) -> None:
        tombs_by_id = self._tombstones_by_id("edge")
        live_nodes = {node.id for node in self._lww_nodes_unlocked()}
        for stem in discover_shard_stems(self.paths.edges_dir):
            grouped: dict[str, list[EdgeRecord]] = defaultdict(list)
            for rec in self._iter_edge_records(stem):
                grouped[rec.id].append(rec)
            lines: list[str] = []
            for record_id, lives in grouped.items():
                picked = resolve(lives, tombs_by_id.get(record_id, []))
                if picked is None or picked.is_tombstone:
                    continue
                assert isinstance(picked.payload, EdgeRecord)
                edge = picked.payload
                if edge.from_id in live_nodes and edge.to_id in live_nodes:
                    lines.append(edge.to_jsonl())
            replace_jsonl(self.paths.edges_dir / f"{stem}.jsonl", lines)

    def _compact_vectors(self) -> None:
        if not self.paths.vectors_dir.is_dir():
            return
        for prop_dir in sorted(self.paths.vectors_dir.iterdir()):
            if not prop_dir.is_dir() or not is_vector_property_name(prop_dir.name):
                continue
            tombs_by_id: dict[str, list[Tombstone]] = defaultdict(list)
            for tomb in self._iter_tombstones():
                if tomb.kind == "vector" and tomb.property == prop_dir.name:
                    tombs_by_id[tomb.id].append(tomb)
            for stem in discover_shard_stems(prop_dir):
                grouped: dict[str, list[VectorRecord]] = defaultdict(list)
                for path in shard_jsonl_files(prop_dir, stem):
                    for rec in self._parse_vectors(path, prop_dir.name):
                        grouped[rec.id].append(rec)
                lines: list[str] = []
                for record_id, lives in grouped.items():
                    picked = resolve(lives, tombs_by_id.get(record_id, []))
                    if picked is not None and not picked.is_tombstone:
                        lines.append(picked.payload.to_jsonl())
                replace_jsonl(prop_dir / f"{stem}.jsonl", lines)

    def _compact_tombstones(self) -> None:
        best: dict[tuple[str, str, str | None], Tombstone] = {}
        for tomb in self._iter_tombstones():
            key = (tomb.kind, tomb.id, tomb.property)
            prev = best.get(key)
            if prev is None:
                best[key] = tomb
                continue
            picked = resolve([], [prev, tomb])
            if picked is not None:
                assert isinstance(picked.payload, Tombstone)
                best[key] = picked.payload
        lines = [t.to_jsonl() for t in best.values()]
        replace_jsonl(self.paths.deleted_jsonl, lines)


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
