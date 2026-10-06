# Cypher subset

Power queries on a graph-ted-db folder. Everyday open / put / get is the Python store API in [Getting started](getting-started.md). graph-ted-db also speaks a **documented subset** of Cypher so graph-ted helpers (and Graphiti’s Neo4j dialect, later) can run against a folder. This is not openCypher and not Neo4j.

Unsupported syntax raises `CypherError` with a source position. Prefer adding to this engine over rewriting queries in graph-ted.

## Expand the language

Do **not** special-case individual helper files. Extend the engine:

| Add… | Where |
|---|---|
| A function (`labels`, `coalesce`, …) | `@cypher_fn("name")` in `graph_ted_db/engine/functions.py`. Parser already treats `IDENT(` as a call. |
| An aggregating function | `@cypher_fn("name", aggregating=True)` — handler receives the list of per-row values. |
| A clause (`MATCH`, `WITH`, …) | Parse it in `engine/parser.py` `parse_clause()`, add an AST dataclass in `engine/ast.py`, register `@clause_handler(ThatClause)` in `engine/executor.py`. |
| A pattern feature | `NodePattern` / `RelPattern` + `Executor.match_pattern`. |
| An operator | `parse_cmp` / `parse_add` + `_binary` in `engine/eval.py`. |

Indexes (`graph_ted_db.index.LocalIndex`) sit under `GRAPH_TED_DB_DATA/<graph-id>/index/` (never in the synced folder). MATCH uses the catalog and adjacency list; shards remain the source of truth and are rebuilt on open.

## Supported today

Aimed at `graph-ted/backend/cypher/helpers/*.cypher`.

**Clauses:** `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH` `[DISTINCT]` `ORDER BY` `SKIP` `LIMIT`, `UNWIND`, `RETURN` `[DISTINCT]` `ORDER BY` `SKIP` `LIMIT`, `UNION` / `UNION ALL`, `DELETE`, `DETACH DELETE`, `CREATE`, `MERGE`, `SET` (property, `+=`, `=`, labels, `n:$(expr)`), `CALL db.create.setNodeVectorProperty` / `setRelationshipVectorProperty`, `CALL db.index.fulltext.queryNodes` / `queryRelationships` `YIELD`. `CREATE INDEX` / `CREATE FULLTEXT INDEX` / `DROP` / `SHOW` are accepted no-ops.

**Patterns:** `(n:Label:Label {prop: expr})`, undirected `(a)-[r]-(b)`, directed `(a)-[r:TYPE]->(b)` / `<-`, typed `[:MENTIONS]`, anonymous `()`, `-[r]-`.

**Expressions:** `$params`, property `.`, subscript `expr[index]` (lists, strings, maps), label check `n:Entity`, `AND`/`OR`/`NOT`, `= <> < > <= >=`, `IN`, `IS NULL` / `IS NOT NULL`, `+` (lists append a non-list), `CASE WHEN … THEN … ELSE … END`, list literals, map literals `{k: expr}`, list comprehensions `[x IN expr WHERE pred]`.

**Functions:** `labels`, `type`, `properties`, `elementId` (also `id`), `coalesce`, `size`, `toString`, `toLower`, `toUpper`, `collect` `[DISTINCT]`, `max`, `min`, `count` `[DISTINCT]`, `vector.similarity.cosine`.

**Not yet:** `FOREACH`, subqueries, `SHORTESTPATH`, Lucene-grade BM25 (fulltext is substring token overlap), `IN TRANSACTIONS`. `elementId` is the record UUID. Nested objects in `props` are stored but not walked by `.` beyond one map.

## Node and relationship mapping

| Cypher | graph-ted-db |
|---|---|
| Node labels | `NodeRecord.labels` |
| `n.uuid` | `props.uuid` if set, else record `id` |
| Other `n.foo` | `props.foo` |
| Rel type `[:MENTIONS]` | `EdgeRecord.type` |
| `type(r)` / `r.name` | type vs `props.name` (Graphiti stores HAS_FORM on `r.name`) |
| `elementId(r)` | edge record `id` |
| `DETACH DELETE n` | `delete_node` (tombstone node + incident edges) |

## API

```python
rows = store.execute(
    "MATCH (n:Entity {group_id: $group_id}) RETURN n.name AS name",
    {"group_id": "default"},
    updated_by="alice",  # optional; default "cypher" on writes
)
```

```bash
graph-ted-db cypher ./my-graph --params '{"group_id":"default"}' \
  'MATCH (n:Entity {group_id: $group_id}) RETURN n.name AS name'

graph-ted-db serve ./my-graph   # http://127.0.0.1:8099  — docs/http.md
```
