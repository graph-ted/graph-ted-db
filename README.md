# graph-ted-db

**SQLite for graphs.** A local, file-based graph store: open a folder from Python, put and get nodes and edges, and leave the network off.

The **database** component of **graph-ted**. A standalone, file-based graph store you can drop in as a lightweight substitute for Neo4j or FalkorDB when the job is simple, a prototype, or a trusted workgroup — not a multi-tenant server. A shared folder is the database; OneDrive, rclone, or abraunegg replicate it. Apps query locally. This is the SQLite-shaped niche for graphs.

## Install

**Python 3.10+.** Private dry-run — these names are **not on PyPI**. From a checkout of this repo:

```bash
python -m pip install -e .
```

Or install a wheel you already built, by path:

```bash
python -m pip install /path/to/graphted_db-0.1.0-py3-none-any.whl
```

Import `graph_ted_db`. The console script is `graphted-db` (`graph-ted-db` is the same entry point). Wheelhouse, git tag, and the thin `graphted` meta package are under [Private dry-run](#private-dry-run-not-on-pypi).

## Open, put, get

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
g = GraphStore.open("./my-graph")
alice = g.make_node(labels=["Entity"], props={"name": "Alice"})
bob = g.make_node(labels=["Entity"], props={"name": "Bob"})
edge = g.make_edge(type="KNOWS", from_id=alice.id, to_id=bob.id)
print(g.get_node(alice.id).props["name"])
print(g.get_node(bob.id).props["name"])
print(g.get_edge(edge.id).type)
```

Open `nodes/<shard>.jsonl` in an editor — that file is the store. Putting the folder in OneDrive (or any sync client) is how it becomes shared. Conflict copies of a shard are union-merged on read; you do not need a live OneDrive account to develop (tests drop `00-DESKTOP-conflict-….jsonl` files by hand).

This repo is only the store. It does not include kit UI, agents, or ontology tooling. Cypher and localhost HTTP are optional and come after the Python API.

## Cypher

Power queries use a documented Cypher subset on the same open store. Syntax: [`docs/cypher.md`](docs/cypher.md).

```python
print(g.execute("MATCH (n) RETURN n.name AS name"))
```

```bash
graphted-db cypher ./my-graph 'MATCH (n) RETURN n.name AS name'
```

## Command line

Same store, from the shell, after the install above:

```bash
graphted-db init ./my-graph --name demo

graphted-db put-node ./my-graph --label Entity --prop name=Alice
# prints a JSON record including "id"

graphted-db ls-nodes ./my-graph
graphted-db info ./my-graph
graphted-db doctor ./my-graph
```

`graph-ted-db doctor` repairs torn JSONL, rebuilds `labels.json`, and tombstones dangling edges.

## Optional: localhost HTTP

Serve when another process should run Cypher without importing `graph_ted_db`. One process owns the folder. This is not Bolt (Bolt later) and not a hosted multi-tenant server. Details: [`docs/http.md`](docs/http.md).

| | Windows | macOS / Linux |
|---|---|---|
| One-time setup | double-click `setup.bat` | `./setup.sh` |
| Start the DB | double-click `serve.bat` | `./serve.sh` |
| CLI prompt (`graphted-db …`) | double-click `shell.bat` | `./shell.sh` |

`serve` listens at **http://127.0.0.1:8099**. With no argument it uses `./my-graph` (created if needed). Pass the **same folder** you use with `put-node` / `ls-nodes`, or curl will talk to a different graph:

```bat
serve.bat D:\graphs\test
```

```bash
./serve.sh ./test
```

```bash
graphted-db serve ./my-graph
```

Startup and `GET /health` print the folder, `name`, and node count so you can confirm. In another window:

```bash
curl -s http://127.0.0.1:8099/health
curl -s http://127.0.0.1:8099/cypher -H "Content-Type: application/json" -d "{\"query\":\"MATCH (n) RETURN n.name AS name\"}"
```

On macOS / Linux, if `./setup.sh` is not executable: `chmod +x setup.sh serve.sh shell.sh`.

## Private dry-run (not on PyPI)

Distribution name is **`graphted-db`** (version **0.1.0**). Import package stays **`graph_ted_db`**. These names are not registered on PyPI or TestPyPI. Do not install them from the public index.

A thin meta package **`graphted`** (also **0.1.0**) lives at `packages/graphted/`. It depends on `graphted-db>=0.1.0` and ships no modules. Build both wheels into `./dist` (nothing is uploaded):

```bash
python -m pip install build
make wheels
```

Without Make:

```bash
python -m build --outdir dist
python -m build --outdir dist packages/graphted
```

Install the database wheel by path:

```bash
python -m pip install /path/to/graphted_db-0.1.0-py3-none-any.whl
graphted-db --help
```

Install the database from a private git tag (the repository root is the `graphted-db` project; the tag must already exist, and you need access to the private repo):

```bash
python -m pip install "git+https://github.com/graph-ted/graph-ted-db.git@v0.1.0"
```

Install from a local wheelhouse. `--no-index` keeps pip off PyPI. Put both wheels in that directory before installing the meta package (its only dependency is `graphted-db`):

```bash
python -m pip install --no-index --find-links /path/to/dist graphted-db
python -m pip install --no-index --find-links /path/to/dist graphted
```

## Used by graph-ted standalone

**graph-ted-db** is the local graph store for **graph-ted Standalone** (SQLite-era easy button: no Docker, no Neo4j). The graph-ted app talks to this store through the folder or the localhost HTTP API. Hosted deployments use Neo4j instead and must not run graph-ted-db. Hosted graph-ted may use other stores; this database is still the thing to adopt on its own.

For the product install and `graph-ted up` happy path, see the app docs:

→ [graph-ted `docs/standalone.md`](https://github.com/graph-ted/graph-ted/blob/main/docs/standalone.md)

## Status

You can initialize a graph folder and **put / get / list / delete nodes and edges**, run a **Cypher subset** against a local catalog + adjacency index, and serve that query API on **localhost HTTP**. Data is sharded JSONL with per-record last-write-wins (including OneDrive conflict-copy union). LWW picks one **complete** record when two writers edit the same id; it does not splice fields or leave live edges to missing nodes (`delete_node` detaches). Appends fsync and repair a torn last line after a hard halt. `graph-ted-db doctor` repairs torn JSONL, rebuilds `labels.json`, and tombstones dangling edges. Graphiti can use `GraphTedDbDriver` with an `http://` serve URL (`GRAPH_STORE=graph-ted` + `GRAPH_TED_URL`) or a folder path (in-process). Cypher: [`docs/cypher.md`](docs/cypher.md). HTTP: [`docs/http.md`](docs/http.md). Product overview: [`docs/overview.md`](docs/overview.md).

## Design in brief

- **One folder = one graph** = one share / ACL boundary.
- Canonical data is sharded JSONL (nodes, edges, embeddings). Derived indexes stay on the local machine and are rebuildable.
- Concurrent edits: per-record last-write-wins, with shard **union-merge** so two users appending different records in the same file do not clobber each other. The winner is one **complete** record someone wrote — LWW does not splice `props` or leave live edges pointing at missing nodes.
- Hard halt: appends `fsync`; a torn last line is truncated before the next write so two records cannot fuse into one invalid line. `graph-ted-db doctor` repairs that, rebuilds `labels.json`, and tombstones dangling edges.
- The graph folder is plain files. There is no SQLite file and no other database engine in the folder. “SQLite for graphs” names the niche: open a local folder from your process, the way you would open a SQLite file. Local catalogs and fulltext are in-memory maps plus plain-file caches.
- No Microsoft Graph API. The sync client is an untrusted pipe.
- Process locks live in local app data (`GRAPH_TED_DB_DATA` / `~/.local/share/graph-ted-db`), never in the synced folder.
- Intended as a **low-friction substitute** for Neo4j / FalkorDB in simple, prototype, and trusted-workgroup settings. Not a hosted multi-writer service.

See [`docs/format.md`](docs/format.md) for the on-disk spec.

## Security

graphted-db stores your graph as ordinary files on disk. **It does not encrypt those files.** Security is as strong as the environment you put them in: disk/volume encryption, OS account permissions, backups, sync tools, and whether anything listens on the network.

- **Standalone / airgapped + encrypted volume:** can be very strong — treat the encrypted volume and airgap as your vault.
- **Synced folders, shared machines, or cloud disks:** anyone or any tool with access to that folder can read the graph (including anything you stored in properties).
- **HTTP serve:** defaults to localhost; binding beyond loopback requires an explicit host and a token. That protects the *port*, not the files on disk.

Plan accordingly: choose storage and network to match how sensitive the data is. The library will not make an insecure disk secure.

## Layout

```
graph_ted_db/          # import package (distribution name: graphted-db)
  store/    # on-disk format, shards, LWW merge
  engine/   # Cypher subset (functions + clauses are registries)
  index/    # local derived catalogs (not synced)
  driver/   # Graphiti GraphTedDbDriver (HTTP URL or in-process folder)
  server/   # localhost HTTP daemon (POST /cypher)
packages/graphted/     # thin meta distribution `graphted` (depends on graphted-db)
docs/
  overview.md
  format.md
  cypher.md
  http.md
```

## Versioning

This library is pre-1.0 (current version **0.1.0**). Until **1.0.0**, a minor bump may change the on-disk format, the Cypher subset, the localhost HTTP API, or the public Python and CLI surface; those breaks are called out in the release notes for that version. Patch releases keep the same format and API. From **1.0.0** on, breaking changes bump the major version.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
```
