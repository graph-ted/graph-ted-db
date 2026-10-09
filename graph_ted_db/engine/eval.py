"""Expression evaluator. Walks AST; aggregating calls are handled by WITH/RETURN."""

from __future__ import annotations

from typing import Any

from graph_ted_db.engine.ast import (
    BinaryOp,
    Call,
    Case,
    IsNull,
    LabelCheck,
    ListComp,
    ListLit,
    Literal,
    MapLit,
    Param,
    Prop,
    Subscript,
    UnaryOp,
    Var,
)
from graph_ted_db.engine.errors import CypherError
from graph_ted_db.engine.functions import lookup
from graph_ted_db.engine.values import NodeView, RelView, identity_key


def evaluate(expr: Any, row: dict[str, Any], params: dict[str, Any]) -> Any:
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Param):
        if expr.name not in params:
            raise CypherError(f"missing parameter ${expr.name}")
        return params[expr.name]
    if isinstance(expr, Var):
        if expr.name not in row:
            raise CypherError(f"unknown variable {expr.name}")
        return row[expr.name]
    if isinstance(expr, Prop):
        target = evaluate(expr.target, row, params)
        if target is None:
            return None
        if isinstance(target, (NodeView, RelView)):
            return target.get(expr.key)
        if isinstance(target, dict):
            return target.get(expr.key)
        raise CypherError(f"cannot read property {expr.key} from {type(target).__name__}")
    if isinstance(expr, Subscript):
        return _subscript(
            evaluate(expr.target, row, params),
            evaluate(expr.index, row, params),
        )
    if isinstance(expr, LabelCheck):
        target = evaluate(expr.target, row, params)
        if target is None:
            return False
        if not isinstance(target, NodeView):
            raise CypherError("label check requires a node")
        return target.has_label(expr.label)
    if isinstance(expr, IsNull):
        value = evaluate(expr.expr, row, params)
        is_null = value is None
        return (not is_null) if expr.negated else is_null
    if isinstance(expr, UnaryOp):
        value = evaluate(expr.expr, row, params)
        if expr.op == "NOT":
            return not _truth(value)
        raise CypherError(f"unsupported unary op {expr.op}")
    if isinstance(expr, BinaryOp):
        return _binary(expr, row, params)
    if isinstance(expr, Call):
        spec = lookup(expr.name)
        if spec.aggregating:
            raise CypherError(f"{expr.name}() is aggregating and can only appear in WITH/RETURN")
        args = [evaluate(arg, row, params) for arg in expr.args]
        return spec.handler(*args)
    if isinstance(expr, Case):
        for cond, result in expr.whens:
            if _truth(evaluate(cond, row, params)):
                return evaluate(result, row, params)
        if expr.else_expr is None:
            return None
        return evaluate(expr.else_expr, row, params)
    if isinstance(expr, ListLit):
        return [evaluate(item, row, params) for item in expr.items]
    if isinstance(expr, MapLit):
        return {key: evaluate(value, row, params) for key, value in expr.items}
    if isinstance(expr, ListComp):
        source = evaluate(expr.source, row, params)
        if source is None:
            return None
        if not isinstance(source, (list, tuple)):
            raise CypherError("list comprehension source must be a list")
        out: list[Any] = []
        for item in source:
            inner = dict(row)
            inner[expr.var] = item
            if expr.where is not None and not _truth(evaluate(expr.where, inner, params)):
                continue
            mapped = item if expr.map_expr is None else evaluate(expr.map_expr, inner, params)
            out.append(mapped)
        return out
    raise CypherError(f"unsupported expression {type(expr).__name__}")


def aggregating_call(expr: Any) -> Call | None:
    """If expr is (or wraps) an aggregating call, return that call."""
    if isinstance(expr, Call) and lookup(expr.name).aggregating:
        return expr
    return None


def contains_aggregate(expr: Any) -> bool:
    if isinstance(expr, Call) and lookup(expr.name).aggregating:
        return True
    for child in _children(expr):
        if contains_aggregate(child):
            return True
    return False


def _children(expr: Any) -> list[Any]:
    if isinstance(expr, IsNull):
        return [expr.expr]
    if isinstance(expr, Prop):
        return [expr.target]
    if isinstance(expr, Subscript):
        return [expr.target, expr.index]
    if isinstance(expr, LabelCheck):
        return [expr.target]
    if isinstance(expr, UnaryOp):
        return [expr.expr]
    if isinstance(expr, BinaryOp):
        return [expr.left, expr.right]
    if isinstance(expr, Call):
        return list(expr.args)
    if isinstance(expr, Case):
        out: list[Any] = []
        for cond, result in expr.whens:
            out.extend((cond, result))
        if expr.else_expr is not None:
            out.append(expr.else_expr)
        return out
    if isinstance(expr, ListLit):
        return list(expr.items)
    if isinstance(expr, MapLit):
        return [v for _, v in expr.items]
    if isinstance(expr, ListComp):
        return (
            [expr.source]
            + ([expr.where] if expr.where else [])
            + ([expr.map_expr] if expr.map_expr else [])
        )
    return []


def eval_aggregate(expr: Any, rows: list[dict[str, Any]], params: dict[str, Any]) -> Any:
    """Evaluate an expression that may contain aggregating calls over `rows`."""
    call = aggregating_call(expr)
    if call is not None:
        spec = lookup(call.name)
        if call.star:
            return len(rows)
        if not call.args:
            raise CypherError(f"{call.name}() needs an argument")
        values = [evaluate(call.args[0], row, params) for row in rows]
        try:
            return spec.handler(values, distinct=call.distinct)
        except TypeError:
            return spec.handler(values)
    if not contains_aggregate(expr):
        if not rows:
            return evaluate(expr, {}, params)
        return evaluate(expr, rows[0], params)
    raise CypherError(
        "aggregating expressions must be a direct collect()/max()/min()/count() call "
        "(wrap other logic inside the aggregate argument)"
    )


def _binary(expr: BinaryOp, row: dict[str, Any], params: dict[str, Any]) -> Any:
    if expr.op == "AND":
        return _truth(evaluate(expr.left, row, params)) and _truth(
            evaluate(expr.right, row, params)
        )
    if expr.op == "OR":
        return _truth(evaluate(expr.left, row, params)) or _truth(evaluate(expr.right, row, params))
    left = evaluate(expr.left, row, params)
    right = evaluate(expr.right, row, params)
    if expr.op == "IN":
        if right is None:
            return None
        if not isinstance(right, (list, tuple)):
            raise CypherError("IN requires a list on the right")
        return any(_eq(left, item) for item in right)
    if expr.op in {"=", "<>", "<", ">", "<=", ">="}:
        if left is None or right is None:
            return None
        if expr.op == "=":
            return _eq(left, right)
        if expr.op == "<>":
            return not _eq(left, right)
        try:
            left_key = _cmp_key(left)
            right_key = _cmp_key(right)
            if expr.op == "<":
                return left_key < right_key
            if expr.op == ">":
                return left_key > right_key
            if expr.op == "<=":
                return left_key <= right_key
            return left_key >= right_key
        except TypeError as exc:
            raise CypherError(
                f"cannot compare {type(left).__name__} with {type(right).__name__}"
            ) from exc
    if expr.op == "+":
        return _plus(left, right)
    if expr.op == "-":
        if left is None or right is None:
            return None
        return left - right
    if expr.op == "*":
        if left is None or right is None:
            return None
        return left * right
    if expr.op == "/":
        if left is None or right is None:
            return None
        return left / right
    raise CypherError(f"unsupported operator {expr.op}")


def _subscript(target: Any, index: Any) -> Any:
    if target is None or index is None:
        return None
    if isinstance(target, dict):
        return target.get(index)
    if isinstance(target, (list, tuple, str)):
        if isinstance(index, bool) or not isinstance(index, (int, float)):
            raise CypherError(f"list index must be a number, got {type(index).__name__}")
        i = int(index)
        length = len(target)
        if i < 0:
            i += length
        if i < 0 or i >= length:
            return None
        return target[i]
    raise CypherError(f"cannot index {type(target).__name__}")


def _plus(left: Any, right: Any) -> Any:
    if left is None or right is None:
        if isinstance(left, list) or isinstance(right, list):
            left_list = left if isinstance(left, list) else ([] if left is None else [left])
            right_list = right if isinstance(right, list) else ([] if right is None else [right])
            return left_list + right_list
        return None
    if isinstance(left, list) and isinstance(right, list):
        return left + right
    if isinstance(left, list):
        return left + [right]
    if isinstance(right, list):
        return [left] + right
    if isinstance(left, str) or isinstance(right, str):
        return str(left) + str(right)
    return left + right


def _eq(left: Any, right: Any) -> bool:
    if isinstance(left, (NodeView, RelView)) or isinstance(right, (NodeView, RelView)):
        return identity_key(left) == identity_key(right)
    return _cmp_key(left) == _cmp_key(right)


def _cmp_key(value: Any) -> Any:
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _truth(value: Any) -> bool:
    return bool(value)
