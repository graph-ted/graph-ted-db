# Vendored graph-ted helper queries

Copies of the graph-ted app's `backend/cypher/helpers/*.cypher`, taken from
graph-ted commit `6555824`. `tests/test_cypher_helpers.py` runs them against a
Graphiti-shaped graph-ted-db folder, so CI checks that the openCypher subset
covers what the app sends, without needing the app repository.

To refresh, copy the files from a graph-ted checkout and update the commit
above:

```bash
cp ../graph-ted/backend/cypher/helpers/*.cypher tests/fixtures/graph_ted_helpers/
```

To test against a live checkout without copying, set `HELPER_DIR` to its
`backend/cypher/helpers` folder.
