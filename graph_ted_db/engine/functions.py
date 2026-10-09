"""Built-in Cypher functions. Add a function with @cypher_fn — no parser change."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from graph_ted_db.engine.errors import CypherError
from graph_ted_db.engine.values import NodeView, RelView


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    handler: Callable[..., Any]
    aggregating: bool = False


FUNCTIONS: dict[str, FunctionSpec] = {}


def cypher_fn(*names: str, aggregating: bool = False) -> Callable:
    """Register a query function. Names are matched case-insensitively."""

    def deco(fn: Callable) -> Callable:
        spec = FunctionSpec(names[0], fn, aggregating)
        for name in names:
            FUNCTIONS[name.lower()] = spec
        return fn

    return deco


def lookup(name: str) -> FunctionSpec:
    spec = FUNCTIONS.get(name.lower())
    if spec is None:
        known = ", ".join(sorted(FUNCTIONS)) or "(none)"
        raise CypherError(
            f"unknown function {name}(); register it with @cypher_fn in "
            f"graph_ted_db.engine.functions (known: {known})"
        )
    return spec


def is_aggregating_name(name: str) -> bool:
    spec = FUNCTIONS.get(name.lower())
    return bool(spec and spec.aggregating)


@cypher_fn("labels")
def fn_labels(node: Any) -> list[str] | None:
    if node is None:
        return None
    if not isinstance(node, NodeView):
        raise CypherError("labels() expects a node")
    return list(node.labels)


@cypher_fn("type")
def fn_type(rel: Any) -> str | None:
    if rel is None:
        return None
    if not isinstance(rel, RelView):
        raise CypherError("type() expects a relationship")
    return rel.type


@cypher_fn("properties")
def fn_properties(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    if isinstance(item, NodeView):
        return item.properties()
    if isinstance(item, RelView):
        return item.properties()
    raise CypherError("properties() expects a node or relationship")


@cypher_fn("elementId", "elementid", "id")
def fn_element_id(item: Any) -> str | None:
    if item is None:
        return None
    if isinstance(item, (NodeView, RelView)):
        return item.id
    raise CypherError("elementId() expects a node or relationship")


@cypher_fn("coalesce")
def fn_coalesce(*args: Any) -> Any:
    for arg in args:
        if arg is not None:
            return arg
    return None


@cypher_fn("size")
def fn_size(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, str, dict)):
        return len(value)
    raise CypherError("size() expects a list, string, or map")


@cypher_fn("toString", "tostring")
def fn_to_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@cypher_fn("toLower", "tolower")
def fn_to_lower(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).lower()


@cypher_fn("toUpper", "toupper")
def fn_to_upper(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).upper()


@cypher_fn("trim")
def fn_trim(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


@cypher_fn("lTrim", "ltrim")
def fn_ltrim(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).lstrip()


@cypher_fn("rTrim", "rtrim")
def fn_rtrim(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).rstrip()


@cypher_fn("replace")
def fn_replace(value: Any, find: Any, repl: Any) -> str | None:
    if value is None:
        return None
    return str(value).replace(str(find), "" if repl is None else str(repl))


@cypher_fn("split")
def fn_split(value: Any, delim: Any) -> list[str] | None:
    if value is None:
        return None
    return str(value).split("" if delim is None else str(delim))


@cypher_fn("substring")
def fn_substring(value: Any, start: Any, length: Any = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    begin = int(start or 0)
    if length is None:
        return text[begin:]
    return text[begin : begin + int(length)]


@cypher_fn("toFloat", "tofloat")
def fn_to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@cypher_fn("toInteger", "tointeger")
def fn_to_integer(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@cypher_fn("vector.similarity.cosine")
def fn_vector_cosine(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    a = [float(v) for v in left]
    b = [float(v) for v in right]
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@cypher_fn("collect", aggregating=True)
def fn_collect(values: list[Any], *, distinct: bool = False) -> list[Any]:
    from graph_ted_db.engine.values import identity_key

    out: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value is None:
            continue
        if distinct:
            key = identity_key(value)
            if key in seen:
                continue
            seen.add(key)
        out.append(value)
    return out


@cypher_fn("max", aggregating=True)
def fn_max(values: list[Any]) -> Any:
    comparable = [v for v in values if v is not None]
    if not comparable:
        return None
    return max(comparable)


@cypher_fn("min", aggregating=True)
def fn_min(values: list[Any]) -> Any:
    comparable = [v for v in values if v is not None]
    if not comparable:
        return None
    return min(comparable)


@cypher_fn("count", aggregating=True)
def fn_count(values: list[Any], *, distinct: bool = False) -> int:
    from graph_ted_db.engine.values import identity_key

    if not distinct:
        return sum(1 for v in values if v is not None)
    seen: set[Any] = set()
    n = 0
    for value in values:
        if value is None:
            continue
        key = identity_key(value)
        if key in seen:
            continue
        seen.add(key)
        n += 1
    return n
