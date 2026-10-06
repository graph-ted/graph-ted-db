# graphted-db

**A local graph database in a folder.** Import it from Python, open a path, read and write nodes and edges — no server required. Network is optional.

Distribution name **`graphted-db`**. Import **`graph_ted_db`**. Part of the [graph-ted](https://github.com/graph-ted/graph-ted) stack, and useful on its own for prototypes, tools, and trusted workgroups that want a property graph without standing up Neo4j or another hosted store.

## Why this exists

- **Minimal friction.** One dependency, one folder, a short Python API.
- **Data you can see.** Nodes and edges live as ordinary files (sharded JSONL). Sync the folder with OneDrive, rclone, or anything else if you want a shared graph.
- **Query when you need it.** Optional Cypher subset on the same open store; optional localhost HTTP for other processes.
- **Familiar on-ramps.** Boring primary API for humans and agents; NetworkX-like aliases if that helps first contact; Cypher when you want Neo4j-ish thinking.

Not a multi-tenant cloud database. Not encrypted at rest by the library (see [Security](#security)).

## Install

**Python 3.10+.** Not on public PyPI yet — install from a checkout or a wheel path:

```bash
python -m pip install -e .
# or: python -m pip install /path/to/graphted_db-0.1.0-py3-none-any.whl
```

CLI entry point: `graphted-db` (alias `graph-ted-db`). Building wheels and the thin `graphted` meta package: see [Building wheels](#building-wheels).

## Quick start (Python)

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
g = GraphStore.open("./my-graph")

alice = g.make_node(labels=["Person"], props={"name": "Alice"})
bob = g.make_node(labels=["Person"], props={"name": "Bob"})
g.make_edge(type="KNOWS", from_id=alice.id, to_id=bob.id)

print(g.get_node(alice.id).props["name"])
for node in g.iter_nodes():
    print(node.labels, node.props)
```

That’s the whole happy path: init → open → put/get/iter. No ports, no Docker.

### NetworkX-like aliases (optional)

Same store, familiar names. Prefer `make_node` / `get_node` / `execute` in new code. Use Cypher (`execute`) when you want Neo4j-ish queries. Graph algorithms such as `shortest_path` are not part of this API.

```python
alice = g.add_node("alice", label="Person", name="Alice")
bob = g.add_node("bob", label="Person", name="Bob")
g.add_edge("alice", "bob", type="KNOWS", since=2020)
assert bob.id in list(g.neighbors("alice"))
```

- **Ids.** A UUID string is the record id. Any other `str` or `int` maps to a stable record id (`GraphStore.alias_record_id`). `nodes()`, `edges()`, and `neighbors()` yield those record ids — the same ids as `iter_nodes` / `iter_edges` — and yield ids, not full records. Alias methods accept either the original key or the record id.
- **Labels.** `label="Person"` or `labels=["Person", "Entity"]` set node labels and are omitted from `props`. Every other keyword is a property (`name="Alice"` becomes `props["name"]`). Calling `add_node` again with the same id replaces the whole record.
- **Edges.** Directed. `type` is the relationship type (default `RELATED`) and is omitted from `props`. Both endpoints must already exist; each `add_edge` inserts another edge. `has_edge(u, v)` is true when `get_edge` would return a live edge from `u` to `v` (`type=` narrows that). `remove_edge(u, v)` deletes one matching edge. `neighbors(n)` yields outgoing neighbor record ids from the local adjacency index.
- **Deletes.** `remove_node` / `remove_edge` call `delete_node` / `delete_edge`. `remove_node` detaches incident edges. A missing node or edge raises `KeyError`.

### Cypher (optional)

```python
print(g.execute("MATCH (n) RETURN n.name AS name"))
```

Full subset: [`docs/cypher.md`](docs/cypher.md).

## Command line

```bash
graphted-db init ./my-graph --name demo
graphted-db put-node ./my-graph --label Person --prop name=Alice
graphted-db ls-nodes ./my-graph
graphted-db info ./my-graph
graphted-db doctor ./my-graph
graphted-db cypher ./my-graph 'MATCH (n) RETURN n.name AS name'
```

## Optional: localhost HTTP

Serve Cypher for another process without importing the library. Defaults to **127.0.0.1:8099**. Not a hosted multi-writer service. Guide: [`docs/http.md`](docs/http.md).

```bash
graphted-db serve ./my-graph
# Windows / macOS helpers: serve.bat / ./serve.sh (after setup.bat / ./setup.sh)
```

## Security

graphted-db stores your graph as ordinary files. **It does not encrypt those files.** Security matches your storage and network: disk/volume encryption, OS permissions, sync exposure, and whether anything listens on the network.

- Airgapped machine + encrypted volume can be very strong.
- Synced or shared folders: anyone with folder access can read properties you stored.
- HTTP serve protects the port (localhost by default; token required off-loopback), not the files on disk.

Plan accordingly.

## How it works (short)

- **One folder = one graph** (one share / ACL boundary).
- Canonical data: sharded JSONL (nodes, edges, embeddings). Local indexes are rebuildable and not the source of truth.
- Concurrent edits: per-record last-write-wins; conflict-copy shards are union-merged on read.
- Crash safety: appends fsync; `doctor` repairs torn lines and dangling edges.
- Process locks live outside the synced folder (`GRAPH_TED_DB_DATA` / `~/.local/share/graph-ted-db`).

On-disk format: [`docs/format.md`](docs/format.md). Overview: [`docs/overview.md`](docs/overview.md).

## In the graph-ted stack

Used as the local store for **graph-ted Standalone**. Hosted graph-ted may use Neo4j or other stores instead. This repo is the database only — no kit UI or agents.

→ App standalone docs: [graph-ted `docs/standalone.md`](https://github.com/graph-ted/graph-ted/blob/main/docs/standalone.md)

## Building wheels

```bash
python -m pip install build
make wheels   # or: python -m build --outdir dist && python -m build --outdir dist packages/graphted
```

Not registered on PyPI/TestPyPI yet. Install by path or private git tag when you have access.

## Versioning

Pre-1.0 (**0.1.0**). Until 1.0.0, minor bumps may change format, Cypher, HTTP, or public Python/CLI — called out in release notes. Patch releases keep format and API. From 1.0.0, breaking changes bump major.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
```

CI runs pytest on pull requests and pushes to `main`.

## Layout

```
graph_ted_db/       # import package (dist: graphted-db)
  store/ engine/ index/ driver/ server/
packages/graphted/  # thin meta package `graphted`
docs/
```
