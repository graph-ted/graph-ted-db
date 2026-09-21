"""Cypher subset lexer. Keywords are case-insensitive; unknown words stay IDENT."""

from __future__ import annotations

from dataclasses import dataclass

from graph_ted_db.engine.errors import CypherError

KEYWORDS = {
    "MATCH",
    "OPTIONAL",
    "WHERE",
    "WITH",
    "UNWIND",
    "RETURN",
    "ORDER",
    "BY",
    "ASC",
    "DESC",
    "DELETE",
    "DETACH",
    "AND",
    "OR",
    "NOT",
    "IN",
    "IS",
    "NULL",
    "AS",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "DISTINCT",
    "TRUE",
    "FALSE",
    "XOR",
    "CREATE",
    "MERGE",
    "SET",
    "CALL",
    "YIELD",
    "INDEX",
    "FULLTEXT",
    "EXISTS",
    "FOR",
    "ON",
    "EACH",
    "IF",
    "DROP",
    "CONSTRAINT",
    "UNIQUE",
    "SHOW",
    "REMOVE",
    "UNION",
    "ALL",
    "LIMIT",
    "SKIP",
}


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    pos: int


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            i += 2
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            end = source.find("*/", i + 2)
            if end < 0:
                raise CypherError("unterminated block comment", pos=i)
            i = end + 2
            continue
        if ch == "'":
            i, text = _string(source, i)
            tokens.append(Token("STRING", text, i))
            continue
        if ch == '"':
            i, text = _dstring(source, i)
            tokens.append(Token("STRING", text, i))
            continue
        if ch == "$":
            start = i
            i += 1
            if i < n and source[i] == "(":
                tokens.append(Token("$", "$", start))
                continue
            if i >= n or not _is_ident_start(source[i]):
                raise CypherError("expected parameter name after $", pos=start)
            i, name = _ident(source, i)
            tokens.append(Token("PARAM", name, start))
            continue
        if ch.isdigit():
            start = i
            i, number = _number(source, i)
            tokens.append(Token("NUMBER", number, start))
            continue
        if _is_ident_start(ch):
            start = i
            i, name = _ident(source, i)
            upper = name.upper()
            if upper in KEYWORDS:
                tokens.append(Token(upper, upper, start))
            else:
                tokens.append(Token("IDENT", name, start))
            continue
        if source.startswith("+=", i):
            tokens.append(Token("+=", "+=", i))
            i += 2
            continue
        if source.startswith("<>", i) or source.startswith("<=", i) or source.startswith(">=", i):
            tokens.append(Token(source[i : i + 2], source[i : i + 2], i))
            i += 2
            continue
        if source.startswith("->", i):
            tokens.append(Token("->", "->", i))
            i += 2
            continue
        if source.startswith("<-", i):
            tokens.append(Token("<-", "<-", i))
            i += 2
            continue
        if ch in "=<>+-*/()[]{},:|.":
            tokens.append(Token(ch, ch, i))
            i += 1
            continue
        raise CypherError(f"unexpected character {ch!r}", pos=i)
    tokens.append(Token("EOF", None, n))
    return tokens


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_ident_cont(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _ident(source: str, i: int) -> tuple[int, str]:
    start = i
    i += 1
    while i < len(source) and _is_ident_cont(source[i]):
        i += 1
    return i, source[start:i]


def _string(source: str, i: int) -> tuple[int, str]:
    start = i
    i += 1
    out: list[str] = []
    while i < len(source):
        ch = source[i]
        if ch == "'":
            if i + 1 < len(source) and source[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return i + 1, "".join(out)
        out.append(ch)
        i += 1
    raise CypherError("unterminated string", pos=start)


def _dstring(source: str, i: int) -> tuple[int, str]:
    start = i
    i += 1
    out: list[str] = []
    while i < len(source):
        ch = source[i]
        if ch == '"':
            if i + 1 < len(source) and source[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            return i + 1, "".join(out)
        out.append(ch)
        i += 1
    raise CypherError("unterminated string", pos=start)


def _number(source: str, i: int) -> tuple[int, int | float]:
    start = i
    while i < len(source) and source[i].isdigit():
        i += 1
    if i < len(source) and source[i] == "." and i + 1 < len(source) and source[i + 1].isdigit():
        i += 1
        while i < len(source) and source[i].isdigit():
            i += 1
        return i, float(source[start:i])
    return i, int(source[start:i])
