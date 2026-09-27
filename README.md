# graph-ted-db

The **database** component of **graph-ted**. A standalone, file-based graph store you can drop in as a lightweight substitute for Neo4j or FalkorDB when the job is simple, a prototype, or a trusted workgroup — not a multi-tenant server.

A shared folder is the database; OneDrive, rclone, or abraunegg replicate it. Apps query locally. This is the SQLite-shaped niche for graphs.

**graph-ted** (the kit) is the larger app layer this database is meant to sit under. This repo is only the store: initialize a folder, put/get nodes and edges, speak a Cypher subset, and optionally serve it on localhost HTTP (Bolt later). It does not include kit UI, agents, or ontology tooling.


## Used by graph-ted standalone

This database is the **default graph store** for the graph-ted **standalone** (laptop) path: `GRAPH_STORE=graph-ted-db` + localhost HTTP (`:8099`).

Full kit install (app + agent + engine + this DB): see **[graph-ted `docs/standalone.md`](https://github.com/graph-ted/graph-ted/blob/main/docs/standalone.md)**.

Hard rule: do **not** point Railway / hosted Compose at graph-ted-db — hosted stays on Neo4j/FalkorDB.


v1 targets Graphiti-style temporal graphs, a documented Cypher subset, and a Python library with an optional localhost HTTP daemon. Bolt is designed for but not built yet.

## Status

You can initialize a graph folder and **put / get / list / delete nodes and edges**, run a **Cypher subset** against a local catalog + adjacency index, and serve that query API on **localhost HTTP**. Data is sharded JSONL with per-record last-write-wins (including OneDrive conflict-copy union). LWW picks one **complete** record when two writers edit the same id; it does not splice fields or leave live edges to missing nodes (`delete_node` detaches). Appends fsync and repair a torn last line after a hard halt. `graph-ted-db doctor` repairs torn JSONL, rebuilds `labels.json`, and tombstones dangling edges. Graphiti can use `GraphTedDbDriver` with an `http://` serve URL (`GRAPH_STORE=graph-ted` + `GRAPH_TED_URL`) or a folder path (in-process). Cypher: [`docs/cypher.md`](docs/cypher.md). HTTP: [`docs/http.md`](docs/http.md).

## Quick start

You need **Python 3.10+**. Then run the setup script once, then start the database.

| | Windows | macOS / Linux |
|---|---|---|
| One-time setup | double-click `setup.bat` | `./setup.sh` |
| Start the DB | double-click `serve.bat` | `./serve.sh` |
| CLI prompt (`graph-ted-db …`) | double-click `shell.bat` | `./shell.sh` |

`serve` listens at **http://127.0.0.1:8099**. With no argument it uses `./my-graph` (created if needed). Pass the **same folder** you use with `put-node` / `ls-nodes`, or curl will talk to a different graph:

```bat
serve.bat D:\graphs\test
```

```bash
./serve.sh ./test
```

Startup and `GET /health` print the folder, `name`, and node count so you can confirm. In another window:

```bash
curl -s http://127.0.0.1:8099/health
curl -s http://127.0.0.1:8099/cypher -H "Content-Type: application/json" -d "{\"query\":\"MATCH (n) RETURN n.name AS name\"}"
```

On macOS / Linux, if `./setup.sh` is not executable: `chmod +x setup.sh serve.sh shell.sh`.

### Manual install

```bash
python -m pip install -e ".[dev]"

graph-ted-db init ./my-graph --name demo

graph-ted-db put-node ./my-graph --label Entity --prop name=Alice
# prints a JSON record including "id"

graph-ted-db ls-nodes ./my-graph
graph-ted-db info ./my-graph
graph-ted-db doctor ./my-graph
graph-ted-db cypher ./my-graph 'MATCH (n) RETURN n.name AS name'
graph-ted-db serve ./my-graph
# another terminal:
#   curl -s http://127.0.0.1:8099/health
#   curl -s http://127.0.0.1:8099/cypher -H 'Content-Type: application/json' \
#     -d '{"query":"MATCH (n) RETURN n.name AS name"}'
```

From Python:

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
g = GraphStore.open("./my-graph")
alice = g.make_node(labels=["Entity"], props={"name": "Alice"})
print(g.get_node(alice.id).props)
print(g.execute("MATCH (n) RETURN n.name AS name"))
```

Open `nodes/<shard>.jsonl` in an editor — that file is the store. Putting the folder in OneDrive (or any sync client) is how it becomes shared. Conflict copies of a shard are union-merged on read; you do not need a live OneDrive account to develop (tests drop `00-DESKTOP-conflict-….jsonl` files by hand).

## Design in brief

- **One folder = one graph** = one share / ACL boundary.
- Canonical data is sharded JSONL (nodes, edges, embeddings). Derived indexes stay on the local machine and are rebuildable.
- Concurrent edits: per-record last-write-wins, with shard **union-merge** so two users appending different records in the same file do not clobber each other. The winner is one **complete** record someone wrote — LWW does not splice `props` or leave live edges pointing at missing nodes.
- Hard halt: appends `fsync`; a torn last line is truncated before the next write so two records cannot fuse into one invalid line. `graph-ted-db doctor` repairs that, rebuilds `labels.json`, and tombstones dangling edges.
- No SQLite (or any other database engine). Local catalogs and fulltext are in-memory maps plus plain-file caches.
- No Microsoft Graph API. The sync client is an untrusted pipe.
- Process locks live in local app data (`GRAPH_TED_DB_DATA` / `~/.local/share/graph-ted-db`), never in the synced folder.
- Intended as a **low-friction substitute** for Neo4j / FalkorDB in simple, prototype, and trusted-workgroup settings. Not a hosted multi-writer service.

See [`docs/format.md`](docs/format.md) for the on-disk spec.

## Layout

```
graph_ted_db/
  store/    # on-disk format, shards, LWW merge
  engine/   # Cypher subset (functions + clauses are registries)
  index/    # local derived catalogs (not synced)
  driver/   # Graphiti GraphTedDbDriver (HTTP URL or in-process folder)
  server/   # localhost HTTP daemon (POST /cypher)
docs/
  format.md
  cypher.md
  http.md
```

## Development

```bash
python -m pip install -e ".[dev]"
pytest
```
