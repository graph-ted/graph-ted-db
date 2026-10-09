"""openCypher subset engine. Expand by registering functions and clause handlers."""

from graph_ted_db.engine.errors import CypherError
from graph_ted_db.engine.functions import cypher_fn, lookup
from graph_ted_db.engine.parser import parse_query


def execute_cypher(
    store, query: str, parameters: dict | None = None, updated_by: str = "cypher"
) -> list[dict]:
    from graph_ted_db.engine.executor import Executor

    ast = parse_query(query)
    return Executor(store, parameters or {}, updated_by=updated_by).run(ast)


__all__ = ["CypherError", "cypher_fn", "execute_cypher", "lookup", "parse_query"]
