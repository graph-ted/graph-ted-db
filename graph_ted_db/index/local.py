"""In-memory node/edge catalog and adjacency, rebuilt from shards.

Persisted under GRAPH_TED_DB_DATA so a restart can skip a full scan. Shards
remain the source of truth; a stale or missing catalog is rebuilt.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from graph_ted_db.store.jsonl import replace_json_file, replace_jsonl
from graph_ted_db.store.records import EdgeRecord, NodeRecord

INDEX_VERSION = 1

Direction = Literal["out", "in", "both"]


@dataclass(frozen=True)
class AdjEntry:
    edge_id: str
    neighbor_id: str
    type: str
    outgoing: bool

    def matches(self, rel_type: str | None, direction: Direction) -> bool:
        if rel_type and self.type != rel_type:
            return False
        if direction == "both":
            return True
        if direction == "out":
            return self.outgoing
        return not self.outgoing


@dataclass
class LocalIndex:
    directory: Path
    nodes: dict[str, NodeRecord] = field(default_factory=dict)
    edges: dict[str, EdgeRecord] = field(default_factory=dict)
    labels: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    uuid_to_id: dict[str, str] = field(default_factory=dict)
    adj: dict[str, list[AdjEntry]] = field(default_factory=lambda: defaultdict(list))

    def rebuild(self, nodes: Iterable[NodeRecord], edges: Iterable[EdgeRecord]) -> None:
        self.nodes.clear()
        self.edges.clear()
        self.labels = defaultdict(set)
        self.uuid_to_id.clear()
        self.adj = defaultdict(list)
        for node in nodes:
            self.upsert_node(node)
        for edge in edges:
            self.upsert_edge(edge)

    def upsert_node(self, record: NodeRecord) -> None:
        previous = self.nodes.get(record.id)
        if previous is not None:
            self._drop_node_index(previous)
        self.nodes[record.id] = record
        for label in record.labels:
            self.labels[label].add(record.id)
        uuid = _node_uuid(record)
        self.uuid_to_id[uuid] = record.id

    def upsert_edge(self, record: EdgeRecord) -> None:
        previous = self.edges.get(record.id)
        if previous is not None:
            self._drop_edge_adj(previous)
        self.edges[record.id] = record
        self.adj[record.from_id].append(
            AdjEntry(record.id, record.to_id, record.type, outgoing=True)
        )
        self.adj[record.to_id].append(
            AdjEntry(record.id, record.from_id, record.type, outgoing=False)
        )

    def remove_node(self, record_id: str) -> None:
        record = self.nodes.pop(record_id, None)
        if record is not None:
            self._drop_node_index(record)
        for entry in list(self.adj.get(record_id, ())):
            self.remove_edge(entry.edge_id)
        self.adj.pop(record_id, None)

    def remove_edge(self, record_id: str) -> None:
        record = self.edges.pop(record_id, None)
        if record is not None:
            self._drop_edge_adj(record)

    def _drop_node_index(self, record: NodeRecord) -> None:
        for label in record.labels:
            ids = self.labels.get(label)
            if ids is not None:
                ids.discard(record.id)
        uuid = _node_uuid(record)
        if self.uuid_to_id.get(uuid) == record.id:
            del self.uuid_to_id[uuid]

    def _drop_edge_adj(self, record: EdgeRecord) -> None:
        for node_id in (record.from_id, record.to_id):
            entries = self.adj.get(node_id)
            if not entries:
                continue
            self.adj[node_id] = [e for e in entries if e.edge_id != record.id]

    def node_by_uuid(self, uuid: str) -> NodeRecord | None:
        record_id = self.uuid_to_id.get(uuid)
        if record_id is None:
            return None
        return self.nodes.get(record_id)

    def nodes_with_label(self, label: str) -> list[NodeRecord]:
        return [self.nodes[i] for i in self.labels.get(label, ()) if i in self.nodes]

    def neighbors(
        self,
        node_id: str,
        *,
        rel_type: str | None = None,
        direction: Direction = "both",
    ) -> list[tuple[EdgeRecord, NodeRecord]]:
        out: list[tuple[EdgeRecord, NodeRecord]] = []
        for entry in self.adj.get(node_id, ()):
            if not entry.matches(rel_type, direction):
                continue
            edge = self.edges.get(entry.edge_id)
            neighbor = self.nodes.get(entry.neighbor_id)
            if edge is None or neighbor is None:
                continue
            out.append((edge, neighbor))
        return out

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        replace_json_file(
            self.directory / "meta.json",
            {"index_version": INDEX_VERSION, "nodes": len(self.nodes), "edges": len(self.edges)},
        )
        node_lines = [_node_catalog_line(n) for n in self.nodes.values()]
        edge_lines = [_edge_catalog_line(e) for e in self.edges.values()]
        replace_jsonl(self.directory / "catalog.jsonl", node_lines + edge_lines)
        adj_lines = []
        for node_id, entries in self.adj.items():
            adj_lines.append(
                json.dumps(
                    {
                        "id": node_id,
                        "edges": [
                            {
                                "edge_id": e.edge_id,
                                "neighbor": e.neighbor_id,
                                "type": e.type,
                                "out": e.outgoing,
                            }
                            for e in entries
                        ],
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            )
        replace_jsonl(self.directory / "adj.jsonl", adj_lines)

    def load(self) -> bool:
        meta_path = self.directory / "meta.json"
        catalog_path = self.directory / "catalog.jsonl"
        if not meta_path.is_file() or not catalog_path.is_file():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if not isinstance(meta, dict) or meta.get("index_version") != INDEX_VERSION:
            return False
        return False  # shards are truth; open always rebuilds. load is a hook for later.


def _node_uuid(record: NodeRecord) -> str:
    raw = record.props.get("uuid")
    if isinstance(raw, str) and raw:
        return raw
    return record.id


def _node_catalog_line(record: NodeRecord) -> str:
    payload: dict[str, Any] = {
        "id": record.id,
        "kind": "node",
        "labels": list(record.labels),
        "name": record.props.get("name"),
        "group_id": record.props.get("group_id"),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _edge_catalog_line(record: EdgeRecord) -> str:
    payload: dict[str, Any] = {
        "id": record.id,
        "kind": "edge",
        "type": record.type,
        "from": record.from_id,
        "to": record.to_id,
        "group_id": record.props.get("group_id"),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
