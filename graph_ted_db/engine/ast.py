"""AST for the openCypher subset. New clauses/expressions are new dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Query:
    clauses: tuple[Any, ...]


@dataclass(frozen=True)
class UnionQuery:
    parts: tuple[Query, ...]
    all: bool = True


@dataclass(frozen=True)
class NodePattern:
    name: str | None
    labels: tuple[str, ...]
    props: tuple[tuple[str, Any], ...]  # (key, Expr)


@dataclass(frozen=True)
class RelPattern:
    name: str | None
    types: tuple[str, ...]
    direction: str  # 'out' | 'in' | 'both'
    props: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Pattern:
    nodes: tuple[NodePattern, ...]
    rels: tuple[RelPattern, ...]


@dataclass(frozen=True)
class ReturnItem:
    expr: Any
    alias: str | None


@dataclass(frozen=True)
class OrderKey:
    expr: Any
    descending: bool = False


@dataclass(frozen=True)
class MatchClause:
    optional: bool
    pattern: Pattern
    where: Any | None = None


@dataclass(frozen=True)
class WhereClause:
    expr: Any


@dataclass(frozen=True)
class WithClause:
    distinct: bool
    items: tuple[ReturnItem, ...]
    where: Any | None = None
    order_by: tuple[OrderKey, ...] = field(default_factory=tuple)
    skip: Any | None = None
    limit: Any | None = None


@dataclass(frozen=True)
class ReturnClause:
    distinct: bool
    items: tuple[ReturnItem, ...]
    order_by: tuple[OrderKey, ...] = field(default_factory=tuple)
    skip: Any | None = None
    limit: Any | None = None


@dataclass(frozen=True)
class UnwindClause:
    expr: Any
    alias: str


@dataclass(frozen=True)
class DeleteClause:
    detach: bool
    expr: Any


@dataclass(frozen=True)
class MergeClause:
    pattern: Pattern


@dataclass(frozen=True)
class CreateClause:
    pattern: Pattern


@dataclass(frozen=True)
class SetItem:
    """One SET assignment: property, map replace/merge, or labels."""

    kind: str  # prop | replace | merge | labels | dyn_labels
    variable: str
    key: str | None = None
    expr: Any | None = None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetClause:
    items: tuple[SetItem, ...]


@dataclass(frozen=True)
class CallClause:
    name: str
    args: tuple[Any, ...]
    yields: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class NoopClause:
    """DDL / unknown CALL that we accept and ignore (CREATE INDEX, etc.)."""

    kind: str = "ddl"


# --- expressions ---


@dataclass(frozen=True)
class Literal:
    value: Any


@dataclass(frozen=True)
class Param:
    name: str


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Prop:
    target: Any
    key: str


@dataclass(frozen=True)
class Subscript:
    """List/map access: expr[index]."""

    target: Any
    index: Any


@dataclass(frozen=True)
class LabelCheck:
    target: Any
    label: str


@dataclass(frozen=True)
class UnaryOp:
    op: str
    expr: Any


@dataclass(frozen=True)
class BinaryOp:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Any, ...]
    distinct: bool = False
    star: bool = False  # count(*)


@dataclass(frozen=True)
class Case:
    whens: tuple[tuple[Any, Any], ...]
    else_expr: Any | None = None


@dataclass(frozen=True)
class ListLit:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class MapLit:
    items: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class ListComp:
    var: str
    source: Any
    where: Any | None = None
    map_expr: Any | None = None


@dataclass(frozen=True)
class IsNull:
    expr: Any
    negated: bool = False
