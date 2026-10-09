"""Recursive-descent parser. Add a clause by extending parse_clause()."""

from __future__ import annotations

from graph_ted_db.engine.ast import (
    BinaryOp,
    Call,
    CallClause,
    Case,
    CreateClause,
    DeleteClause,
    IsNull,
    LabelCheck,
    ListComp,
    ListLit,
    Literal,
    MapLit,
    MatchClause,
    MergeClause,
    NodePattern,
    NoopClause,
    OrderKey,
    Param,
    Pattern,
    Prop,
    Query,
    RelPattern,
    ReturnClause,
    ReturnItem,
    SetClause,
    SetItem,
    Subscript,
    UnaryOp,
    UnionQuery,
    UnwindClause,
    Var,
    WhereClause,
    WithClause,
)
from graph_ted_db.engine.errors import CypherError
from graph_ted_db.engine.lexer import Token, tokenize


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    @property
    def cur(self) -> Token:
        return self.tokens[self.i]

    def at(self, *kinds: str) -> bool:
        return self.cur.kind in kinds

    def eat(self, kind: str) -> Token:
        if self.cur.kind != kind:
            raise CypherError(f"expected {kind}, got {self.cur.kind}", pos=self.cur.pos)
        tok = self.cur
        self.i += 1
        return tok

    def match(self, *kinds: str) -> Token | None:
        if self.cur.kind in kinds:
            tok = self.cur
            self.i += 1
            return tok
        return None

    def eat_name(self) -> str:
        """Identifier, or a keyword used as a name (db.index.fulltext…)."""
        if self.at("IDENT"):
            return str(self.eat("IDENT").value)
        if self.cur.kind.isalpha() and self.cur.kind not in ("EOF",):
            tok = self.cur
            self.i += 1
            if isinstance(tok.value, str) and tok.value:
                return tok.value
            return tok.kind
        raise CypherError(
            f"expected IDENT, got {self.cur.kind}", pos=self.cur.pos
        )

    def parse(self) -> Query | UnionQuery:
        parts: list[Query] = []
        union_all = True
        while True:
            clauses: list[object] = []
            while not self.at("EOF", "UNION"):
                clauses.append(self.parse_clause())
            if not clauses:
                raise CypherError("empty query", pos=self.cur.pos)
            parts.append(Query(tuple(clauses)))
            if not self.at("UNION"):
                break
            self.eat("UNION")
            if self.match("ALL") is None:
                union_all = False
        if len(parts) == 1:
            return parts[0]
        return UnionQuery(tuple(parts), all=union_all)

    def parse_clause(self) -> object:
        if self.at("OPTIONAL"):
            return self.parse_match()
        if self.at("MATCH"):
            return self.parse_match()
        if self.at("WHERE"):
            self.eat("WHERE")
            return WhereClause(self.parse_or())
        if self.at("WITH"):
            return self.parse_with()
        if self.at("UNWIND"):
            return self.parse_unwind()
        if self.at("RETURN"):
            return self.parse_return()
        if self.at("DETACH"):
            self.eat("DETACH")
            self.eat("DELETE")
            return DeleteClause(True, self.parse_or())
        if self.at("DELETE"):
            self.eat("DELETE")
            return DeleteClause(False, self.parse_or())
        if self.at("MERGE"):
            return self.parse_merge()
        if self.at("CREATE"):
            return self.parse_create_or_ddl()
        if self.at("SET"):
            return self.parse_set()
        if self.at("CALL"):
            return self.parse_call_clause()
        if self.at("DROP", "SHOW", "REMOVE"):
            return self.parse_ddl_rest(self.cur.kind.lower())
        raise CypherError(
            f"unsupported clause starting with {self.cur.kind}; "
            "extend parse_clause() to add it",
            pos=self.cur.pos,
        )

    def parse_match(self) -> MatchClause:
        optional = self.match("OPTIONAL") is not None
        self.eat("MATCH")
        pattern = self.parse_pattern()
        where = None
        if self.at("WHERE"):
            self.eat("WHERE")
            where = self.parse_or()
        return MatchClause(optional, pattern, where)

    def parse_order_skip_limit(
        self,
    ) -> tuple[tuple[OrderKey, ...], object | None, object | None]:
        order: list[OrderKey] = []
        if self.match("ORDER"):
            self.eat("BY")
            order.append(self.parse_order_key())
            while self.match(","):
                order.append(self.parse_order_key())
        skip = None
        if self.match("SKIP"):
            skip = self.parse_or()
        limit = None
        if self.match("LIMIT"):
            limit = self.parse_or()
        return tuple(order), skip, limit

    def parse_with(self) -> WithClause:
        self.eat("WITH")
        distinct = self.match("DISTINCT") is not None
        items = self.parse_return_items()
        where = None
        if self.at("WHERE"):
            self.eat("WHERE")
            where = self.parse_or()
        order, skip, limit = self.parse_order_skip_limit()
        return WithClause(distinct, tuple(items), where, order, skip, limit)

    def parse_return(self) -> ReturnClause:
        self.eat("RETURN")
        distinct = self.match("DISTINCT") is not None
        items = self.parse_return_items()
        order, skip, limit = self.parse_order_skip_limit()
        return ReturnClause(distinct, tuple(items), order, skip, limit)

    def parse_order_key(self) -> OrderKey:
        expr = self.parse_or()
        descending = False
        if self.match("DESC"):
            descending = True
        else:
            self.match("ASC")
        return OrderKey(expr, descending)

    def parse_merge(self) -> MergeClause:
        self.eat("MERGE")
        return MergeClause(self.parse_pattern())

    def parse_create_or_ddl(self) -> CreateClause | NoopClause:
        self.eat("CREATE")
        if self.at("INDEX", "FULLTEXT", "CONSTRAINT", "UNIQUE"):
            return self.parse_ddl_rest("create")
        return CreateClause(self.parse_pattern())

    def parse_ddl_rest(self, kind: str) -> NoopClause:
        """Skip the rest of a DDL statement (usually the whole query)."""
        depth = 0
        while not self.at("EOF"):
            if self.at("(", "[", "{"):
                depth += 1
                self.i += 1
                continue
            if self.at(")", "]", "}"):
                depth = max(0, depth - 1)
                self.i += 1
                continue
            if depth == 0 and self.at(
                "MATCH",
                "OPTIONAL",
                "MERGE",
                "WITH",
                "UNWIND",
                "RETURN",
                "DELETE",
                "DETACH",
                "SET",
                "CALL",
                "UNION",
            ):
                break
            if depth == 0 and self.at("CREATE") and self._peek_not_ddl_create():
                break
            self.i += 1
        return NoopClause(kind)

    def _peek_not_ddl_create(self) -> bool:
        nxt = self.tokens[self.i + 1] if self.i + 1 < len(self.tokens) else None
        if nxt is None:
            return True
        return nxt.kind not in ("INDEX", "FULLTEXT", "CONSTRAINT", "UNIQUE")

    def parse_set(self) -> SetClause:
        self.eat("SET")
        items = [self.parse_set_item()]
        while self.match(","):
            items.append(self.parse_set_item())
        return SetClause(tuple(items))

    def parse_set_item(self) -> SetItem:
        variable = str(self.eat("IDENT").value)
        if self.match("."):
            key = str(self.eat("IDENT").value)
            self.eat("=")
            return SetItem(kind="prop", variable=variable, key=key, expr=self.parse_or())
        if self.match("+="):
            return SetItem(kind="merge", variable=variable, expr=self.parse_or())
        if self.match("="):
            return SetItem(kind="replace", variable=variable, expr=self.parse_or())
        if self.at(":"):
            labels: list[str] = []
            dyn = None
            while self.match(":"):
                if self.at("$"):
                    self.eat("$")
                    self.eat("(")
                    dyn = self.parse_or()
                    self.eat(")")
                else:
                    labels.append(str(self.eat("IDENT").value))
            if dyn is not None:
                return SetItem(
                    kind="dyn_labels",
                    variable=variable,
                    expr=dyn,
                    labels=tuple(labels),
                )
            return SetItem(kind="labels", variable=variable, labels=tuple(labels))
        raise CypherError("expected SET assignment", pos=self.cur.pos)

    def parse_call_clause(self) -> CallClause | NoopClause:
        self.eat("CALL")
        parts = [self.eat_name()]
        while self.match("."):
            parts.append(self.eat_name())
        name = ".".join(parts)
        args: list[object] = []
        if self.match("("):
            if not self.at(")"):
                args.append(self.parse_or())
                while self.match(","):
                    args.append(self.parse_or())
            self.eat(")")
        yields: list[tuple[str, str | None]] = []
        if self.match("YIELD"):
            while True:
                yielded = self.eat_name()
                alias = None
                if self.match("AS"):
                    alias = self.eat_name()
                yields.append((yielded, alias))
                if not self.match(","):
                    break
        lower = name.lower()
        if "setnodevectorproperty" in lower or "setrelationshipvectorproperty" in lower:
            return CallClause(name, tuple(args), tuple(yields))
        if "fulltext" in lower or "querynodes" in lower or "queryrelationships" in lower:
            return CallClause(name, tuple(args), tuple(yields))
        if self.at("EOF") or yields:
            return CallClause(name, tuple(args), tuple(yields)) if args or yields else NoopClause("call")
        return CallClause(name, tuple(args), tuple(yields)) if args else self.parse_ddl_rest("call")

    def parse_unwind(self) -> UnwindClause:
        self.eat("UNWIND")
        expr = self.parse_or()
        self.eat("AS")
        alias = self.eat("IDENT").value
        assert isinstance(alias, str)
        return UnwindClause(expr, alias)

    def parse_return_items(self) -> list[ReturnItem]:
        items = [self.parse_return_item()]
        while self.match(","):
            items.append(self.parse_return_item())
        return items

    def parse_return_item(self) -> ReturnItem:
        expr = self.parse_or()
        alias = None
        if self.match("AS"):
            alias = self.eat_name()
        elif isinstance(expr, Var):
            alias = expr.name
        return ReturnItem(expr, alias)

    def parse_pattern(self) -> Pattern:
        nodes = [self.parse_node_pattern()]
        rels: list[RelPattern] = []
        while self.at("-", "<-"):
            rels.append(self.parse_rel_pattern())
            nodes.append(self.parse_node_pattern())
        return Pattern(tuple(nodes), tuple(rels))

    def parse_node_pattern(self) -> NodePattern:
        self.eat("(")
        name: str | None = None
        if self.at("IDENT"):
            ident = self.eat("IDENT").value
            assert isinstance(ident, str)
            name = ident
        labels: list[str] = []
        while self.match(":"):
            labels.append(str(self.eat("IDENT").value))
        props: list[tuple[str, object]] = []
        if self.at("{"):
            props = list(self.parse_map().items)
        self.eat(")")
        return NodePattern(name, tuple(labels), tuple(props))

    def parse_rel_pattern(self) -> RelPattern:
        incoming = self.match("<-") is not None
        if not incoming:
            self.eat("-")
        self.eat("[")
        name: str | None = None
        if self.at("IDENT"):
            ident = self.eat("IDENT").value
            assert isinstance(ident, str)
            name = ident
        types: list[str] = []
        if self.match(":"):
            types.append(str(self.eat("IDENT").value))
            while self.match("|"):
                types.append(str(self.eat("IDENT").value))
        props: tuple[tuple[str, object], ...] = ()
        if self.at("{"):
            props = tuple(self.parse_map().items)
        self.eat("]")
        if self.match("->"):
            if incoming:
                raise CypherError("relationship cannot be both <- and ->", pos=self.cur.pos)
            direction = "out"
        else:
            self.eat("-")
            direction = "in" if incoming else "both"
        return RelPattern(name, tuple(types), direction, props)

    def parse_or(self):
        expr = self.parse_and()
        while self.match("OR"):
            expr = BinaryOp("OR", expr, self.parse_and())
        return expr

    def parse_and(self):
        expr = self.parse_not()
        while self.match("AND"):
            expr = BinaryOp("AND", expr, self.parse_not())
        return expr

    def parse_not(self):
        if self.match("NOT"):
            return UnaryOp("NOT", self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        expr = self.parse_add()
        if self.at("IS"):
            self.eat("IS")
            negated = self.match("NOT") is not None
            self.eat("NULL")
            return IsNull(expr, negated)
        if self.match("IN"):
            return BinaryOp("IN", expr, self.parse_add())
        if self.at("=", "<>", "<", ">", "<=", ">="):
            op = self.cur.kind
            self.i += 1
            return BinaryOp(op, expr, self.parse_add())
        return expr

    def parse_add(self):
        expr = self.parse_mul()
        while self.at("+", "-"):
            op = self.cur.kind
            self.i += 1
            expr = BinaryOp(op, expr, self.parse_mul())
        return expr

    def parse_mul(self):
        expr = self.parse_postfix()
        while self.at("*", "/"):
            op = self.cur.kind
            self.i += 1
            expr = BinaryOp(op, expr, self.parse_postfix())
        return expr

    def parse_postfix(self):
        expr = self.parse_atom()
        while True:
            if self.match("."):
                key = self.eat_name()
                expr = Prop(expr, str(key))
                continue
            if self.match("(") and isinstance(expr, Prop):
                name = _dotted_call_name(expr)
                args: list[object] = []
                if not self.at(")"):
                    args.append(self.parse_or())
                    while self.match(","):
                        args.append(self.parse_or())
                self.eat(")")
                expr = Call(name, tuple(args), False)
                continue
            if self.match(":"):
                label = self.eat_name()
                expr = LabelCheck(expr, str(label))
                continue
            if self.match("["):
                index = self.parse_or()
                self.eat("]")
                expr = Subscript(expr, index)
                continue
            break
        return expr

    def parse_atom(self):
        if self.match("NULL"):
            return Literal(None)
        if self.match("TRUE"):
            return Literal(True)
        if self.match("FALSE"):
            return Literal(False)
        if self.at("STRING", "NUMBER"):
            tok = self.cur
            self.i += 1
            return Literal(tok.value)
        if self.at("PARAM"):
            return Param(str(self.eat("PARAM").value))
        if self.at("CASE"):
            return self.parse_case()
        if self.at("["):
            return self.parse_list()
        if self.at("{"):
            return self.parse_map()
        if self.match("("):
            expr = self.parse_or()
            self.eat(")")
            return expr
        if self.at("IDENT"):
            name = str(self.eat("IDENT").value)
            if self.at("("):
                return self.parse_call(name)
            return Var(name)
        raise CypherError(f"unexpected {self.cur.kind} in expression", pos=self.cur.pos)

    def parse_call(self, name: str) -> Call:
        self.eat("(")
        distinct = self.match("DISTINCT") is not None
        args: list[object] = []
        if not self.at(")"):
            args.append(self.parse_or())
            while self.match(","):
                args.append(self.parse_or())
        self.eat(")")
        return Call(name, tuple(args), distinct)

    def parse_case(self) -> Case:
        self.eat("CASE")
        whens: list[tuple[object, object]] = []
        while self.match("WHEN"):
            cond = self.parse_or()
            self.eat("THEN")
            result = self.parse_or()
            whens.append((cond, result))
        else_expr = None
        if self.match("ELSE"):
            else_expr = self.parse_or()
        self.eat("END")
        if not whens:
            raise CypherError("CASE needs WHEN", pos=self.cur.pos)
        return Case(tuple(whens), else_expr)

    def parse_list(self):
        self.eat("[")
        if self.at("]"):
            self.eat("]")
            return ListLit(())
        if self.at("IDENT"):
            save = self.i
            var_tok = self.eat("IDENT")
            if self.match("IN"):
                var = str(var_tok.value)
                source = self.parse_or()
                where = None
                mapped = None
                if self.match("WHERE"):
                    where = self.parse_or()
                if self.match("|"):
                    mapped = self.parse_or()
                self.eat("]")
                return ListComp(var, source, where, mapped)
            self.i = save
        items = [self.parse_or()]
        while self.match(","):
            items.append(self.parse_or())
        self.eat("]")
        return ListLit(tuple(items))

    def parse_map(self) -> MapLit:
        self.eat("{")
        items: list[tuple[str, object]] = []
        if not self.at("}"):
            items.append(self.parse_map_item())
            while self.match(","):
                items.append(self.parse_map_item())
        self.eat("}")
        return MapLit(tuple(items))

    def parse_map_item(self) -> tuple[str, object]:
        if self.at("STRING"):
            key = str(self.eat("STRING").value)
        else:
            key = self.eat_name()
        self.eat(":")
        return key, self.parse_or()


def _dotted_call_name(expr: object) -> str:
    parts: list[str] = []
    cur: object = expr
    while isinstance(cur, Prop):
        parts.append(cur.key)
        cur = cur.target
    if isinstance(cur, Var):
        parts.append(cur.name)
    else:
        raise CypherError("dotted call needs a name path")
    parts.reverse()
    return ".".join(parts)


def parse_query(source: str) -> Query | UnionQuery:
    return Parser(tokenize(source)).parse()
