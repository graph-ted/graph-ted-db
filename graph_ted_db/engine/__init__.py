"""openCypher subset engine. Expand by registering functions and clause handlers."""

from graph_ted_db.engine.errors import CypherError
from graph_ted_db.engine.functions import cypher_fn, lookup
from graph_ted_db.engine.parser import parse_query


def execute_cypher(
    store, query: str, parameters: dict | None = None, updated_by: str = "cypher"
) -> list[dict]:
    from graph_ted_db.engine.executor import Executor
    from graph_ted_db.engine.values import bind_vector_lookup, reset_vector_lookup

    # Load embeddings before any node view is built, so property reads see the
    # vectors stored beside the node. The lookup returns the live bucket.
    store._ensure_vector_map_unlocked()
    token = bind_vector_lookup(store._vector_bucket_unlocked)
    try:
        ast = parse_query(query)
        return Executor(store, parameters or {}, updated_by=updated_by).run(ast)
    finally:
        reset_vector_lookup(token)


__all__ = ["CypherError", "cypher_fn", "execute_cypher", "lookup", "parse_query"]
