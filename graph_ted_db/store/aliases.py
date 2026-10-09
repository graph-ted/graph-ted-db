"""Thin NetworkX-familiar names for GraphStore.

These methods are a secondary path onto the same records as ``make_node``,
``put_node``, ``iter_nodes``, and ``execute``. They are not a NetworkX
implementation: there is no algorithm API, no attribute view, and no
undirected graph.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid5

from graph_ted_db.store.format import normalize_uuid
from graph_ted_db.store.records import EdgeRecord, NodeRecord, Tombstone

# uuid5(uuid.NAMESPACE_URL, "https://github.com/graph-ted/graph-ted-db/nx-alias")
_ALIAS_NAMESPACE = UUID("0c346375-9278-58a6-9053-cdddec04b268")

DEFAULT_REL_TYPE = "RELATED"


def alias_record_id(node_key: str | int) -> str:
    """Map a NetworkX-style node key to a graph-ted-db record id.

    A ``str`` that is already a UUID (hyphenated or 32 hex digits) is that
    record id. Any other ``str`` becomes ``uuid5`` of ``s:<text>``. An ``int``
    (not ``bool``) becomes ``uuid5`` of ``i:<decimal>``. The namespace is
    ``_ALIAS_NAMESPACE`` above, so the same key always hits the same record.
    """
    if isinstance(node_key, str):
        try:
            return normalize_uuid(node_key)
        except ValueError:
            material = f"s:{node_key}"
    elif isinstance(node_key, int) and not isinstance(node_key, bool):
        material = f"i:{node_key}"
    else:
        raise TypeError(
            "NetworkX-style node id must be a str or int "
            "(UUID strings are record ids; other strings and ints map to a stable UUID)"
        )
    return str(uuid5(_ALIAS_NAMESPACE, material))


def _labels_from_attrs(attrs: dict[str, Any]) -> tuple[str, ...]:
    """Pull ``label`` / ``labels`` out of alias attrs. They are not properties."""
    label = attrs.pop("label", None)
    labels = attrs.pop("labels", None)
    out: list[str] = []
    if label is not None:
        if not isinstance(label, str) or not label:
            raise TypeError("label must be a non-empty string")
        out.append(label)
    if labels is not None:
        if isinstance(labels, str):
            seq: list[Any] = [labels]
        elif isinstance(labels, (list, tuple)):
            seq = list(labels)
        else:
            raise TypeError("labels must be a string or a list/tuple of strings")
        for item in seq:
            if not isinstance(item, str) or not item:
                raise TypeError("labels must be non-empty strings")
            if item not in out:
                out.append(item)
    return tuple(out)


def _updated_by(attrs: dict[str, Any]) -> str:
    updated_by = attrs.pop("updated_by", "")
    if not isinstance(updated_by, str):
        raise TypeError("updated_by must be a string")
    return updated_by


def _rel_type(attrs: dict[str, Any]) -> str:
    """Relationship type for ``add_edge``. ``type`` is not stored as a property."""
    if "type" not in attrs:
        return DEFAULT_REL_TYPE
    rel = attrs.pop("type")
    if rel is None:
        return DEFAULT_REL_TYPE
    if not isinstance(rel, str) or not rel:
        raise TypeError("type must be a non-empty string (relationship type; default RELATED)")
    return rel


def _check_rel_filter(rel_type: str | None) -> None:
    if rel_type is None:
        return
    if not isinstance(rel_type, str) or not rel_type:
        raise TypeError("type must be a non-empty string")


class _GraphStoreAliases:
    """Methods mixed into ``GraphStore``. Callers use the store, not this class."""

    alias_record_id = staticmethod(alias_record_id)

    def add_node(self, node_id: str | int, /, **attrs: Any) -> NodeRecord:
        """Insert or replace a node. ``label`` / ``labels`` are labels; other keywords are props.

        Same id again replaces the whole record (labels and props). It does not
        merge attributes. Returns the stored ``NodeRecord``.
        """
        labels = _labels_from_attrs(attrs)
        updated_by = _updated_by(attrs)
        return self.make_node(
            labels=labels,
            props=attrs,
            record_id=alias_record_id(node_id),
            updated_by=updated_by,
        )

    def add_edge(self, u: str | int, v: str | int, /, **attrs: Any) -> EdgeRecord:
        """Insert a directed edge from ``u`` to ``v``.

        ``type`` is the relationship type (default ``RELATED``) and is not a
        property. Both endpoints must already be live nodes. Each call inserts
        another edge (parallel edges are kept). Returns the stored ``EdgeRecord``.
        """
        rel = _rel_type(attrs)
        updated_by = _updated_by(attrs)
        src = alias_record_id(u)
        dst = alias_record_id(v)
        if self.get_node(src) is None:
            raise ValueError(f"add_edge source is not a live node: {u!r}")
        if self.get_node(dst) is None:
            raise ValueError(f"add_edge target is not a live node: {v!r}")
        return self.make_edge(
            type=rel,
            from_id=src,
            to_id=dst,
            props=attrs,
            updated_by=updated_by,
        )

    def nodes(self) -> Iterator[str]:
        """Yield live node record ids. Same ids as ``iter_nodes``, not full records."""
        for record in self.iter_nodes():
            yield record.id

    def edges(self) -> Iterator[tuple[str, str]]:
        """Yield ``(from_id, to_id)`` record-id pairs for live directed edges.

        A pair can repeat when several edges share those endpoints. Relationship
        type and edge id are on the ``EdgeRecord`` from ``iter_edges``.
        """
        for record in self.iter_edges():
            yield (record.from_id, record.to_id)

    def has_node(self, n: str | int) -> bool:
        """True when ``get_node`` returns a live node for this key."""
        return self.get_node(alias_record_id(n)) is not None

    def has_edge(self, u: str | int, v: str | int, *, type: str | None = None) -> bool:
        """True when a live directed edge runs from ``u`` to ``v``.

        Optional ``type`` limits the check to that relationship type. Existence
        follows ``get_edge`` (a hidden or tombstoned edge does not count).
        """
        _check_rel_filter(type)
        return self._alias_edge(u, v, type) is not None

    def neighbors(self, n: str | int) -> Iterator[str]:
        """Yield outgoing neighbor record ids, once each, from the local adjacency index.

        Directed: an edge ``n -> m`` yields ``m``, and the reverse edge does not.
        Missing ``n`` raises ``KeyError``.
        """
        record_id = alias_record_id(n)
        if self.get_node(record_id) is None:
            raise KeyError(n)
        with self._lock():
            self._refresh_index_unlocked()
            index = self._index
            ordered: list[str] = []
            seen: set[str] = set()
            if index is not None:
                for entry in index.adj.get(record_id, ()):
                    if not entry.outgoing or entry.neighbor_id in seen:
                        continue
                    if self._get_node_unlocked(entry.neighbor_id) is None:
                        continue
                    seen.add(entry.neighbor_id)
                    ordered.append(entry.neighbor_id)
            else:
                for edge in self._graph_edges_unlocked():
                    if edge.from_id != record_id or edge.to_id in seen:
                        continue
                    seen.add(edge.to_id)
                    ordered.append(edge.to_id)
        yield from ordered

    def remove_node(self, n: str | int) -> Tombstone:
        """Tombstone the node and its incident edges (``delete_node``).

        Raises ``KeyError`` when the node is not live.
        """
        if not self.has_node(n):
            raise KeyError(n)
        return self.delete_node(alias_record_id(n))

    def remove_edge(self, u: str | int, v: str | int, *, type: str | None = None) -> Tombstone:
        """Delete one live directed edge from ``u`` to ``v`` (``delete_edge``).

        Optional ``type`` selects the relationship type. When several edges
        match, one of them is deleted. Raises ``KeyError`` when none match.
        """
        _check_rel_filter(type)
        edge = self._alias_edge(u, v, type)
        if edge is None:
            raise KeyError((u, v) if type is None else (u, v, type))
        return self.delete_edge(edge.id)

    def _alias_edge(self, u: str | int, v: str | int, rel_type: str | None) -> EdgeRecord | None:
        src = alias_record_id(u)
        dst = alias_record_id(v)
        for edge in self.iter_edges():
            if edge.from_id != src or edge.to_id != dst:
                continue
            if rel_type is not None and edge.type != rel_type:
                continue
            if self.get_edge(edge.id) is not None:
                return edge
        return None
