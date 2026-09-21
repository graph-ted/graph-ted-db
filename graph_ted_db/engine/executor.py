"""Clause pipeline. Register a new clause with @clause_handler."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from graph_ted_db.engine.ast import (
    CallClause,
    CreateClause,
    DeleteClause,
    MatchClause,
    MergeClause,
    NodePattern,
    NoopClause,
    Pattern,
    RelPattern,
    ReturnClause,
    ReturnItem,
    SetClause,
    UnionQuery,
    UnwindClause,
    WhereClause,
    WithClause,
)
from graph_ted_db.engine.errors import CypherError
from graph_ted_db.engine.eval import _truth, contains_aggregate, eval_aggregate, evaluate
from graph_ted_db.engine.values import (
    NodeView,
    RelView,
    identity_key,
    jsonish,
    node_view,
    rel_view,
)
from graph_ted_db.index.local import Direction

CLAUSE_HANDLERS: dict[type, Callable] = {}


def clause_handler(cls: type) -> Callable:
    def deco(fn: Callable) -> Callable:
        CLAUSE_HANDLERS[cls] = fn
        return fn

    return deco


class Executor:
    def __init__(
        self, store: Any, params: dict[str, Any], updated_by: str = "cypher"
    ) -> None:
        self.store = store
        self.params = params
        self.updated_by = updated_by
        index = getattr(store, "_index", None)
        if index is None:
            raise CypherError("graph index is not built; open the store first")
        self.index = index

    def run(self, query) -> list[dict[str, Any]]:
        if isinstance(query, UnionQuery):
            out: list[dict[str, Any]] = []
            seen: set[Any] = set()
            for part in query.parts:
                for row in self._run_part(part):
                    if query.all:
                        out.append(row)
                        continue
                    key = identity_key(list(row.values()))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(row)
            return out
        return self._run_part(query)

    def _run_part(self, query) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = [{}]
        returned: list[dict[str, Any]] | None = None
        for clause in query.clauses:
            handler = CLAUSE_HANDLERS.get(type(clause))
            if handler is None:
                raise CypherError(
                    f"no executor for {type(clause).__name__}; "
                    "add @clause_handler in graph_ted_db.engine.executor"
                )
            result = handler(self, clause, rows)
            if isinstance(clause, ReturnClause):
                returned = result
            else:
                rows = result
        if returned is None:
            return []
        return returned


@clause_handler(MatchClause)
def handle_match(ex: Executor, clause: MatchClause, rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        matched = ex.match_pattern(row, clause.pattern)
        if clause.where is not None:
            matched = [r for r in matched if _truth(evaluate(clause.where, r, ex.params))]
        if matched:
            out.extend(matched)
        elif clause.optional:
            null_row = dict(row)
            for name in _pattern_names(clause.pattern):
                if name not in row:
                    null_row[name] = None
            out.append(null_row)
    return out


@clause_handler(WhereClause)
def handle_where(ex: Executor, clause: WhereClause, rows: list[dict]) -> list[dict]:
    return [r for r in rows if _truth(evaluate(clause.expr, r, ex.params))]


@clause_handler(WithClause)
def handle_with(ex: Executor, clause: WithClause, rows: list[dict]) -> list[dict]:
    projected = _project(ex, rows, clause.items, distinct=clause.distinct)
    if clause.where is not None:
        projected = [r for r in projected if _truth(evaluate(clause.where, r, ex.params))]
    pairs = [(row, row) for row in projected]
    pairs = _order_pairs(ex, pairs, clause.order_by)
    sliced = _slice_pairs(ex, pairs, clause.skip, clause.limit)
    return [projected_row for projected_row, _ in sliced]


@clause_handler(ReturnClause)
def handle_return(ex: Executor, clause: ReturnClause, rows: list[dict]) -> list[dict]:
    projected_pairs: list[tuple[dict, dict]] = []
    if any(contains_aggregate(it.expr) for it in clause.items):
        groups = _group_rows(ex, rows, clause.items)
        for group in groups:
            new = dict(group["vals"])
            for item in clause.items:
                if contains_aggregate(item.expr):
                    new[_alias(item)] = eval_aggregate(item.expr, group["rows"], ex.params)
            src = group["rows"][0] if group["rows"] else {}
            projected_pairs.append((new, {**src, **new}))
    else:
        seen: set[Any] = set()
        for row in rows:
            new = {_alias(item): evaluate(item.expr, row, ex.params) for item in clause.items}
            if clause.distinct:
                key = identity_key([new[_alias(item)] for item in clause.items])
                if key in seen:
                    continue
                seen.add(key)
            projected_pairs.append((new, {**row, **new}))
    projected_pairs = _order_pairs(ex, projected_pairs, clause.order_by)
    projected_pairs = _slice_pairs(ex, projected_pairs, clause.skip, clause.limit)
    return [_graphiti_record(projected) for projected, _ in projected_pairs]


@clause_handler(UnwindClause)
def handle_unwind(ex: Executor, clause: UnwindClause, rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        value = evaluate(clause.expr, row, ex.params)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)):
            raise CypherError("UNWIND expects a list")
        for item in value:
            nxt = dict(row)
            nxt[clause.alias] = item
            out.append(nxt)
    return out


@clause_handler(NoopClause)
def handle_noop(_ex: Executor, _clause: NoopClause, rows: list[dict]) -> list[dict]:
    return rows


@clause_handler(MergeClause)
def handle_merge(ex: Executor, clause: MergeClause, rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        out.extend(_upsert_pattern(ex, row, clause.pattern, create_only=False))
    return out


@clause_handler(CreateClause)
def handle_create(ex: Executor, clause: CreateClause, rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        out.extend(_upsert_pattern(ex, row, clause.pattern, create_only=True))
    return out


@clause_handler(SetClause)
def handle_set(ex: Executor, clause: SetClause, rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        nxt = dict(row)
        for item in clause.items:
            nxt = _apply_set_item(ex, nxt, item)
        out.append(nxt)
    return out


@clause_handler(CallClause)
def handle_call(ex: Executor, clause: CallClause, rows: list[dict]) -> list[dict]:
    lower = clause.name.lower()
    if "setnodevectorproperty" in lower:
        for row in rows:
            _call_set_vector(ex, row, clause.args, rel=False)
        return rows
    if "setrelationshipvectorproperty" in lower:
        for row in rows:
            _call_set_vector(ex, row, clause.args, rel=True)
        return rows
    if "querynodes" in lower or "queryrelationships" in lower:
        return _call_fulltext(ex, clause, rows)
    return rows


def _order_pairs(
    ex: Executor,
    pairs: list[tuple[dict, dict]],
    order_by,
) -> list[tuple[dict, dict]]:
    if not order_by:
        return pairs

    def sort_key(pair: tuple[dict, dict]) -> tuple:
        _, combined = pair
        parts = []
        for key in order_by:
            value = evaluate(key.expr, combined, ex.params)
            parts.append(_SortKey(value, key.descending))
        return tuple(parts)

    return sorted(pairs, key=sort_key)


def _slice_pairs(
    ex: Executor,
    pairs: list[tuple[dict, dict]],
    skip,
    limit,
) -> list[tuple[dict, dict]]:
    start = 0
    if skip is not None:
        start = int(evaluate(skip, {}, ex.params) or 0)
    end: int | None = None
    if limit is not None:
        end = start + int(evaluate(limit, {}, ex.params) or 0)
    return pairs[start:end]


def _call_fulltext(ex: Executor, clause: CallClause, rows: list[dict]) -> list[dict]:
    from graph_ted_db.engine.fulltext import query_nodes, query_relationships

    ctx = rows[0] if rows else {}
    index_name = str(evaluate(clause.args[0], ctx, ex.params) if clause.args else "")
    query_text = str(evaluate(clause.args[1], ctx, ex.params) if len(clause.args) > 1 else "")
    limit = None
    if len(clause.args) > 2:
        opts = evaluate(clause.args[2], ctx, ex.params)
        if isinstance(opts, dict) and opts.get("limit") is not None:
            limit = int(opts["limit"])
        elif isinstance(opts, (int, float)):
            limit = int(opts)
    lower = clause.name.lower()
    if "queryrelationships" in lower:
        hits = query_relationships(ex.index, index_name, query_text, limit)
        defaults = ("relationship", "score")
    else:
        hits = query_nodes(ex.index, index_name, query_text, limit)
        defaults = ("node", "score")
    aliases: list[tuple[str, str]] = []
    if clause.yields:
        for name, alias in clause.yields:
            aliases.append((name, alias or name))
    else:
        aliases = [(name, name) for name in defaults]
    out: list[dict] = []
    for hit in hits:
        nxt = dict(ctx)
        for source, dest in aliases:
            if source in hit:
                nxt[dest] = hit[source]
        out.append(nxt)
    return out


@clause_handler(DeleteClause)
def handle_delete(ex: Executor, clause: DeleteClause, rows: list[dict]) -> list[dict]:
    for row in rows:
        target = evaluate(clause.expr, row, ex.params)
        if target is None:
            continue
        if isinstance(target, NodeView):
            ex.store._delete_node_unlocked(target.id, updated_by=ex.updated_by)
        elif isinstance(target, RelView):
            ex.store._delete_edge_unlocked(target.id, updated_by=ex.updated_by)
        else:
            raise CypherError("DELETE expects a node or relationship")
    return rows


# Graphiti EntityNode/EpisodicNode reject JSON null for these RETURN columns.
_GRAPHITI_NULL_COLUMNS = {
    "created_at": "1970-01-01T00:00:00+00:00",
    "summary": "",
    "content": "",
    "source": "text",
    "source_description": "",
    "fact": "",
    "entity_edges": [],
    "episodes": [],
    "attributes": {},
}


def _graphiti_record(projected: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in projected.items():
        encoded = jsonish(value)
        if encoded is None and key in _GRAPHITI_NULL_COLUMNS:
            encoded = _GRAPHITI_NULL_COLUMNS[key]
        out[key] = encoded
    return out


def _alias(item: ReturnItem) -> str:
    if item.alias:
        return item.alias
    raise CypherError("RETURN/WITH item needs AS alias")


def _pattern_names(pattern: Pattern) -> list[str]:
    names: list[str] = []
    for node in pattern.nodes:
        if node.name:
            names.append(node.name)
    for rel in pattern.rels:
        if rel.name:
            names.append(rel.name)
    return names


def _project(
    ex: Executor, rows: list[dict], items: tuple[ReturnItem, ...], *, distinct: bool
) -> list[dict]:
    if any(contains_aggregate(it.expr) for it in items):
        out: list[dict] = []
        for group in _group_rows(ex, rows, items):
            new = dict(group["vals"])
            for item in items:
                if contains_aggregate(item.expr):
                    new[_alias(item)] = eval_aggregate(item.expr, group["rows"], ex.params)
            out.append(new)
        return out
    out = []
    seen: set[Any] = set()
    for row in rows:
        new = {_alias(item): evaluate(item.expr, row, ex.params) for item in items}
        if distinct:
            key = identity_key([new[_alias(item)] for item in items])
            if key in seen:
                continue
            seen.add(key)
        out.append(new)
    return out


def _group_rows(ex: Executor, rows: list[dict], items: tuple[ReturnItem, ...]) -> list[dict]:
    if not rows:
        if items and all(contains_aggregate(it.expr) for it in items):
            return [{"vals": {}, "rows": []}]
        return []
    groups: OrderedDict[tuple, dict] = OrderedDict()
    for row in rows:
        key_parts: list[Any] = []
        vals: dict[str, Any] = {}
        for item in items:
            if contains_aggregate(item.expr):
                continue
            value = evaluate(item.expr, row, ex.params)
            vals[_alias(item)] = value
            key_parts.append(identity_key(value))
        gkey = tuple(key_parts)
        slot = groups.get(gkey)
        if slot is None:
            slot = {"vals": vals, "rows": []}
            groups[gkey] = slot
        slot["rows"].append(row)
    return list(groups.values())


class _SortKey:
    def __init__(self, value: Any, descending: bool) -> None:
        self.value = value
        self.descending = descending

    def __lt__(self, other: _SortKey) -> bool:
        a, b = self.value, other.value
        if a is None and b is None:
            return False
        if a is None:
            return False
        if b is None:
            return True
        try:
            less = a < b
        except TypeError:
            less = str(a) < str(b)
        return (not less) if self.descending else less


def _bind_node(row: dict, pattern: NodePattern, node: NodeView, params: dict) -> bool:
    for label in pattern.labels:
        if not node.has_label(label):
            return False
    for key, expr in pattern.props:
        expected = evaluate(expr, row, params)
        if node.get(key) != expected:
            return False
    if pattern.name:
        existing = row.get(pattern.name)
        if existing is not None and existing != node:
            return False
        row[pattern.name] = node
    return True


def _uuid_from_pattern(ex: Executor, row: dict, pattern: NodePattern) -> str | None:
    for key, expr in pattern.props:
        if key == "uuid":
            value = evaluate(expr, row, ex.params)
            return value if isinstance(value, str) else None
    return None


class _PatternMixin:
    pass


def _node_candidates(ex: Executor, row: dict, pattern: NodePattern) -> list[NodeView]:
    if pattern.name:
        existing = row.get(pattern.name)
        if existing is not None:
            return [existing] if isinstance(existing, NodeView) else []
    uuid = _uuid_from_pattern(ex, row, pattern)
    if uuid:
        record = ex.index.node_by_uuid(uuid)
        return [node_view(record)] if record is not None else []
    if pattern.labels:
        ids: set[str] | None = None
        for label in pattern.labels:
            labeled = {n.id for n in ex.index.nodes_with_label(label)}
            ids = labeled if ids is None else ids & labeled
        records = [ex.index.nodes[i] for i in (ids or ()) if i in ex.index.nodes]
        return [node_view(r) for r in records]
    return [node_view(r) for r in ex.index.nodes.values()]


def _start_index(ex: Executor, row: dict, pattern: Pattern) -> int:
    for i, node in enumerate(pattern.nodes):
        if node.name and isinstance(row.get(node.name), NodeView):
            return i
    for i, node in enumerate(pattern.nodes):
        if _uuid_from_pattern(ex, row, node):
            return i
    return 0


def _rel_types(rel: RelPattern) -> tuple[str, ...] | None:
    return rel.types or None


def _walk_neighbors(
    ex: Executor, src: NodeView, rel: RelPattern, direction: Direction
) -> list[tuple[RelView, NodeView]]:
    types = rel.types
    if not types:
        pairs = ex.index.neighbors(src.id, rel_type=None, direction=direction)
        return [(rel_view(e), node_view(n)) for e, n in pairs]
    out: list[tuple[RelView, NodeView]] = []
    for rel_type in types:
        pairs = ex.index.neighbors(src.id, rel_type=rel_type, direction=direction)
        out.extend((rel_view(e), node_view(n)) for e, n in pairs)
    return out


def _invert(direction: str) -> Direction:
    if direction == "out":
        return "in"
    if direction == "in":
        return "out"
    return "both"


def _bind_rel(row: dict, rel: RelPattern, view: RelView) -> bool:
    if rel.name:
        existing = row.get(rel.name)
        if existing is not None and existing != view:
            return False
        row[rel.name] = view
    return True


def match_pattern(self: Executor, row: dict, pattern: Pattern) -> list[dict]:
    if _should_scan_edges(self, row, pattern):
        return _match_edges(self, row, pattern)
    start = _start_index(self, row, pattern)
    results: list[dict] = []
    for node in _node_candidates(self, row, pattern.nodes[start]):
        base = dict(row)
        if not _bind_node(base, pattern.nodes[start], node, self.params):
            continue
        placed = {start: node}
        _expand(self, base, pattern, placed, results)
    return results


def _end_bound(ex: Executor, row: dict, node: NodePattern) -> bool:
    if node.name and isinstance(row.get(node.name), NodeView):
        return True
    return _uuid_from_pattern(ex, row, node) is not None


def _should_scan_edges(ex: Executor, row: dict, pattern: Pattern) -> bool:
    if len(pattern.rels) != 1 or len(pattern.nodes) != 2:
        return False
    return not _end_bound(ex, row, pattern.nodes[0]) and not _end_bound(
        ex, row, pattern.nodes[1]
    )


def _match_edges(ex: Executor, row: dict, pattern: Pattern) -> list[dict]:
    rel = pattern.rels[0]
    left, right = pattern.nodes
    out: list[dict] = []
    for record in ex.index.edges.values():
        view = rel_view(record)
        if rel.types and view.type not in rel.types:
            continue
        src = ex.index.nodes.get(record.from_id)
        dst = ex.index.nodes.get(record.to_id)
        if src is None or dst is None:
            continue
        src_view, dst_view = node_view(src), node_view(dst)
        if rel.direction == "in":
            pairs = [(dst_view, src_view)]
        elif rel.direction == "out":
            pairs = [(src_view, dst_view)]
        else:
            pairs = [(src_view, dst_view)]
            if (left.labels or right.labels) and record.from_id != record.to_id:
                pairs.append((dst_view, src_view))
        for a, b in pairs:
            nxt = dict(row)
            if not _bind_rel(nxt, rel, view):
                continue
            if not _bind_node(nxt, left, a, ex.params):
                continue
            if not _bind_node(nxt, right, b, ex.params):
                continue
            out.append(nxt)
    return out


def _expand(
    ex: Executor,
    row: dict,
    pattern: Pattern,
    placed: dict[int, NodeView],
    results: list[dict],
) -> None:
    for i, rel in enumerate(pattern.rels):
        left, right = i, i + 1
        if left in placed and right not in placed:
            _walk_to(ex, row, pattern, placed, left, right, rel, invert=False, results=results)
            return
        if right in placed and left not in placed:
            _walk_to(ex, row, pattern, placed, right, left, rel, invert=True, results=results)
            return
    results.append(row)


def _walk_to(
    ex: Executor,
    row: dict,
    pattern: Pattern,
    placed: dict[int, NodeView],
    from_idx: int,
    to_idx: int,
    rel: RelPattern,
    *,
    invert: bool,
    results: list[dict],
) -> None:
    src = placed[from_idx]
    direction: Direction = _invert(rel.direction) if invert else rel.direction  # type: ignore[assignment]
    if rel.direction == "both":
        direction = "both"
    for edge_view, neighbor in _walk_neighbors(ex, src, rel, direction):
        nxt = dict(row)
        if not _bind_rel(nxt, rel, edge_view):
            continue
        if not _bind_node(nxt, pattern.nodes[to_idx], neighbor, ex.params):
            continue
        placed2 = dict(placed)
        placed2[to_idx] = neighbor
        _expand(ex, nxt, pattern, placed2, results)


Executor.match_pattern = match_pattern  # type: ignore[method-assign]


_EMBED_KEYS = frozenset(
    {"name_embedding", "fact_embedding", "embedding", "content_embedding", "summary_embedding"}
)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _strip_embed(props: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    keep: dict[str, Any] = {}
    embeds: dict[str, Any] = {}
    for key, value in props.items():
        if key in _EMBED_KEYS or str(key).endswith("_embedding"):
            embeds[key] = value
        elif key == "labels":
            continue
        else:
            keep[key] = _jsonable(value)
    return keep, embeds


def _persist_node(ex: Executor, view: NodeView) -> NodeView:
    from graph_ted_db.store.format import format_timestamp, normalize_uuid
    from graph_ted_db.store.records import NodeRecord
    from uuid import uuid4

    try:
        nid = normalize_uuid(view.id)
    except ValueError:
        nid = str(uuid4())
    props, embeds = _strip_embed(dict(view.props))
    props.setdefault("uuid", nid)
    rec = NodeRecord(
        id=nid,
        updated_at=format_timestamp(),
        labels=tuple(dict.fromkeys(view.labels)),
        props=props,
        updated_by=ex.updated_by,
    )
    ex.store._put_node_unlocked(rec)
    for prop, vec in embeds.items():
        _store_vector(ex, nid, prop, vec)
    return node_view(rec)


def _persist_edge(ex: Executor, view: RelView) -> RelView:
    from graph_ted_db.store.format import format_timestamp, normalize_uuid
    from graph_ted_db.store.records import EdgeRecord
    from uuid import uuid4

    try:
        eid = normalize_uuid(view.id)
    except ValueError:
        eid = str(uuid4())
    props, embeds = _strip_embed(dict(view.props))
    props.setdefault("uuid", eid)
    rec = EdgeRecord(
        id=eid,
        updated_at=format_timestamp(),
        type=view.type,
        from_id=normalize_uuid(view.from_id),
        to_id=normalize_uuid(view.to_id),
        props=props,
        updated_by=ex.updated_by,
    )
    ex.store._put_edge_unlocked(rec)
    for prop, vec in embeds.items():
        _store_vector(ex, eid, prop, vec)
    return rel_view(rec)


def _store_vector(ex: Executor, record_id: str, prop: str, vec: Any) -> None:
    from graph_ted_db.store.format import format_timestamp, is_vector_property_name
    from graph_ted_db.store.records import VectorRecord

    if not is_vector_property_name(prop):
        return
    values = vec.tolist() if hasattr(vec, "tolist") else vec
    if not isinstance(values, (list, tuple)) or not values:
        return
    try:
        floats = [float(x) for x in values]
    except (TypeError, ValueError):
        return
    rec = VectorRecord.from_floats(
        id=record_id,
        property=prop,
        values=floats,
        updated_at=format_timestamp(),
        updated_by=ex.updated_by,
    )
    ex.store._put_vector_unlocked(rec)


def _eval_props(ex: Executor, row: dict, pattern: NodePattern | RelPattern) -> dict[str, Any]:
    if isinstance(pattern, RelPattern):
        return {}
    out: dict[str, Any] = {}
    for key, expr in pattern.props:
        out[key] = _jsonable(evaluate(expr, row, ex.params))
    return out


def _new_node(ex: Executor, row: dict, pattern: NodePattern) -> NodeView:
    from uuid import uuid4

    props = _eval_props(ex, row, pattern)
    raw_id = props.get("uuid") or str(uuid4())
    view = NodeView(id=str(raw_id), labels=tuple(pattern.labels), props=props)
    return _persist_node(ex, view)


def _upsert_pattern(
    ex: Executor, row: dict, pattern: Pattern, *, create_only: bool
) -> list[dict]:
    nxt = dict(row)
    nodes: list[NodeView] = []
    for node_pat in pattern.nodes:
        uuid = None
        for key, expr in node_pat.props:
            if key == "uuid":
                val = evaluate(expr, nxt, ex.params)
                uuid = str(val) if val is not None else None
        existing = None
        if node_pat.name and isinstance(nxt.get(node_pat.name), NodeView):
            existing = nxt[node_pat.name]
        elif not create_only and uuid:
            rec = ex.index.node_by_uuid(uuid)
            existing = node_view(rec) if rec is not None else None
        if existing is None:
            existing = _new_node(ex, nxt, node_pat)
        elif node_pat.props:
            extra = _eval_props(ex, nxt, node_pat)
            merged = dict(existing.props)
            merged.update(extra)
            labels = tuple(dict.fromkeys(existing.labels + node_pat.labels))
            existing = _persist_node(
                ex, NodeView(id=existing.id, labels=labels, props=merged)
            )
        if node_pat.name:
            nxt[node_pat.name] = existing
        nodes.append(existing)
    for i, rel_pat in enumerate(pattern.rels):
        left, right = nodes[i], nodes[i + 1]
        rel_props: dict[str, Any] = {}
        for key, expr in rel_pat.props:
            rel_props[key] = _jsonable(evaluate(expr, nxt, ex.params))
        rel_uuid = rel_props.get("uuid")
        if rel_uuid is not None:
            rel_uuid = str(rel_uuid)
        existing_edge = None
        if rel_pat.name and isinstance(nxt.get(rel_pat.name), RelView):
            existing_edge = nxt[rel_pat.name]
        rel_type = rel_pat.types[0] if rel_pat.types else "RELATES_TO"
        if existing_edge is None and not create_only and rel_uuid:
            rec = ex.index.edges.get(rel_uuid)
            if rec is None:
                rec = next(
                    (
                        e
                        for e in ex.index.edges.values()
                        if e.props.get("uuid") == rel_uuid
                    ),
                    None,
                )
            if rec is not None:
                existing_edge = rel_view(rec)
        if existing_edge is None and not create_only:
            for edge, _neigh in ex.index.neighbors(
                left.id, rel_type=rel_type, direction="out"
            ):
                ev = rel_view(edge)
                if ev.to_id != right.id:
                    continue
                if rel_uuid and ev.get("uuid") != rel_uuid and ev.id != rel_uuid:
                    continue
                existing_edge = ev
                break
        if existing_edge is None:
            from uuid import uuid4

            eid = rel_uuid or str(uuid4())
            existing_edge = _persist_edge(
                ex,
                RelView(
                    id=eid,
                    type=rel_type,
                    from_id=left.id,
                    to_id=right.id,
                    props={"uuid": eid, **rel_props},
                ),
            )
        if rel_pat.name:
            nxt[rel_pat.name] = existing_edge
    return [nxt]


def _apply_set_item(ex: Executor, row: dict, item: Any) -> dict:
    target = row.get(item.variable)
    if target is None:
        raise CypherError(f"SET unknown variable {item.variable}")
    if item.kind == "prop" and isinstance(target, NodeView):
        props = dict(target.props)
        props[item.key] = _jsonable(evaluate(item.expr, row, ex.params))
        view = _persist_node(ex, NodeView(id=target.id, labels=target.labels, props=props))
        row[item.variable] = view
        return row
    if item.kind == "prop" and isinstance(target, RelView):
        props = dict(target.props)
        props[item.key] = _jsonable(evaluate(item.expr, row, ex.params))
        view = _persist_edge(
            ex,
            RelView(
                id=target.id,
                type=target.type,
                from_id=target.from_id,
                to_id=target.to_id,
                props=props,
            ),
        )
        row[item.variable] = view
        return row
    if item.kind in ("replace", "merge"):
        data = evaluate(item.expr, row, ex.params)
        if not isinstance(data, dict):
            raise CypherError("SET n = map expects a map")
        data = {str(k): v for k, v in data.items()}
        extra_labels = data.get("labels")
        if item.kind == "replace" and isinstance(target, NodeView):
            labels = target.labels
            if isinstance(extra_labels, list):
                labels = tuple(str(x) for x in extra_labels)
            view = _persist_node(
                ex, NodeView(id=target.id, labels=labels, props=dict(data))
            )
            row[item.variable] = view
            return row
        if item.kind == "merge" and isinstance(target, NodeView):
            props = dict(target.props)
            props.update(data)
            view = _persist_node(
                ex, NodeView(id=target.id, labels=target.labels, props=props)
            )
            row[item.variable] = view
            return row
        if isinstance(target, RelView):
            props = dict(target.props) if item.kind == "merge" else {}
            props.update(data)
            view = _persist_edge(
                ex,
                RelView(
                    id=target.id,
                    type=target.type,
                    from_id=target.from_id,
                    to_id=target.to_id,
                    props=props,
                ),
            )
            row[item.variable] = view
            return row
    if item.kind == "labels" and isinstance(target, NodeView):
        labels = tuple(dict.fromkeys(target.labels + item.labels))
        view = _persist_node(ex, NodeView(id=target.id, labels=labels, props=target.props))
        row[item.variable] = view
        return row
    if item.kind == "dyn_labels" and isinstance(target, NodeView):
        extra = evaluate(item.expr, row, ex.params)
        names = list(item.labels)
        if isinstance(extra, str):
            names.extend(x for x in extra.split(":") if x)
        elif isinstance(extra, (list, tuple)):
            names.extend(str(x) for x in extra)
        labels = tuple(dict.fromkeys(list(target.labels) + names))
        view = _persist_node(ex, NodeView(id=target.id, labels=labels, props=target.props))
        row[item.variable] = view
        return row
    raise CypherError(f"unsupported SET {item.kind} on {type(target).__name__}")


def _call_set_vector(ex: Executor, row: dict, args: tuple, *, rel: bool) -> None:
    if len(args) < 3:
        return
    target = evaluate(args[0], row, ex.params)
    prop = evaluate(args[1], row, ex.params)
    vec = evaluate(args[2], row, ex.params)
    if not isinstance(prop, str):
        return
    record_id = getattr(target, "id", None)
    if not isinstance(record_id, str):
        return
    _store_vector(ex, record_id, prop, vec)

