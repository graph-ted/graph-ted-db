<p align="center">
  <img src="https://graph-ted.com/graph-ted-db/docs/assets/logo-readme.png" alt="graph-ted-db" width="340" height="116">
</p>

<!-- Images and links use absolute URLs so they render on PyPI. The logo is served by the docs site. -->

# graph-ted-db

Local property-graph storage for Python. Your data lives in a folder on disk; open it from your process, query it in-process, and optionally sync that folder like any other files. No database server to run for the default path.

**Package:** `graph-ted-db` · **Import:** `graph_ted_db` · **Repo:** [graph-ted/graph-ted-db](https://github.com/graph-ted/graph-ted-db) · **License:** MIT

> Status: the public API and on-disk format below are what we ship. `pip install graph-ted-db` is the intended install; until the package is on PyPI, use a checkout (`pip install -e .`) or a wheel path.

---

## Install

```bash
pip install graph-ted-db
```

Requires Python 3.10+.

## Quick start

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
g = GraphStore.open("./my-graph")

alice = g.make_node(labels=["Person"], props={"name": "Alice"})
bob = g.make_node(labels=["Person"], props={"name": "Bob"})
g.make_edge(type="KNOWS", from_id=alice.id, to_id=bob.id)

print(g.get_node(alice.id).props["name"])
```

CLI:

```bash
graph-ted-db init ./my-graph --name demo --exist-ok
graph-ted-db put-node ./my-graph --label Person --prop name=Alice
graph-ted-db ls-nodes ./my-graph
```

`graphted-db` is an alias for the same command.

Optional HTTP (localhost only by default; see [HTTP docs](https://graph-ted.com/graph-ted-db/docs/http/)):

```bash
graph-ted-db serve ./my-graph
# in another terminal:
curl -s http://127.0.0.1:8099/health
curl -s http://127.0.0.1:8099/cypher -H 'Content-Type: application/json' \
  -d '{"query":"MATCH (n:Person) RETURN n.name AS name"}'
```

## Features

- **Folder = database** — nodes, edges, and embeddings as sharded JSONL; readable, backupable, syncable
- **In-process API** — `put` / `get` / `iter` / `delete` without opening a network port
- **openCypher queries** — `g.execute(...)` runs a documented subset of openCypher for graph-pattern queries ([openCypher subset](https://graph-ted.com/graph-ted-db/docs/cypher/))
- **Optional HTTP** — localhost endpoint for openCypher queries when another process needs the store ([HTTP docs](https://graph-ted.com/graph-ted-db/docs/http/))
- **Sync-friendly** — multiple devices can write at once; last-write-wins per record; conflict-copy shards merged on read
- **NetworkX-style helpers** — `add_node`, `add_edge`, `neighbors`, and related methods on the same `GraphStore` (no algorithm suite)

## What it is and isn't

**It is:**

- A Python library that stores a property graph (nodes, edges, properties, embeddings) as plain JSONL files in one folder.
- In-process: open, read, write and query from your own Python process. No server to install or run.
- A documented subset of openCypher, plus NetworkX-style helpers.
- Sync-friendly across devices: multiple devices can add and edit at the same time; concurrent edits to the same record resolve to the latest version. The folder can live in OneDrive, Dropbox or an `rclone bisync` folder (see [Sharing](https://graph-ted.com/graph-ted-db/docs/sharing/)).
- Small: no runtime dependencies beyond the Python standard library.

**It isn't:**

- A database server, a hosted service or a multi-tenant system. The optional HTTP endpoint is for local processes on the same machine.
- Encrypted. Files are ordinary files; protect them with disk encryption and OS permissions.
- A complete Cypher implementation or a Neo4j replacement. Unsupported clauses fail with an error rather than being ignored.
- A graph-algorithm library. Use NetworkX or similar on exported data.
- Built for very large graphs. The whole graph is loaded into memory when it opens; see the tested sizes in [Overview](https://graph-ted.com/graph-ted-db/docs/overview/).
- Stable across minor versions before 1.0 (see [Versioning](#versioning)).
- Telemetry-enabled. It makes no network calls of its own.

## When to use it

Good fit for prototypes, local tools, agents, and small trusted groups that want a property graph next to the app.

Choose a server graph database when you need multi-tenant hosting, fine-grained remote auth, or a complete query language and enterprise feature set out of the box.

## Security

Data is stored as ordinary files. The library does **not** encrypt the graph folder. Security matches your environment: disk or volume encryption, OS account permissions, backups, sync tools, and whether anything listens on the network.

- **Airgapped host + encrypted volume** — can be very strong; treat that environment as your boundary.
- **Synced or shared folders** — anyone with access to the folder can read what you stored (including properties).
- **HTTP serve** — defaults to localhost; binding beyond loopback requires an explicit host and a token. That protects the port, not the files on disk.

Choose storage and network to match how sensitive the data is.

## Documentation

Docs: [graph-ted.com/graph-ted-db/docs](https://graph-ted.com/graph-ted-db/docs/), built from the `docs/` folder in this repository. Preview locally: see [PREVIEW.md](https://github.com/graph-ted/graph-ted-db/blob/main/PREVIEW.md).

| Doc | Contents |
|-----|----------|
| [Overview](https://graph-ted.com/graph-ted-db/docs/overview/) | Product overview |
| [On-disk format](https://graph-ted.com/graph-ted-db/docs/format/) | On-disk layout |
| [openCypher subset](https://graph-ted.com/graph-ted-db/docs/cypher/) | Supported openCypher subset |
| [Trademarks](https://graph-ted.com/graph-ted-db/docs/trademarks/) | Trademarks |
| [HTTP](https://graph-ted.com/graph-ted-db/docs/http/) | Local HTTP serve |

## Trademarks

openCypher is a trademark of Neo4j, Inc. Other names are trademarks of their respective owners; no endorsement implied. See [Trademarks](https://graph-ted.com/graph-ted-db/docs/trademarks/).

## Versioning

Pre-1.0 (`0.1.0`). Until 1.0.0, minor versions may include breaking changes; they are noted in release notes. From 1.0.0, breaking changes bump the major version.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Feedback and issues

- **Bugs, enhancement requests, docs problems:** [open an issue](https://github.com/graph-ted/graph-ted-db/issues/new/choose) using one of the forms.
- **Questions and ideas:** [Discussions](https://github.com/graph-ted/graph-ted-db/discussions).
- **Security vulnerabilities:** report privately as described in [SECURITY.md](https://github.com/graph-ted/graph-ted-db/blob/main/SECURITY.md), never in a public issue.

Contributing: see [CONTRIBUTING.md](https://github.com/graph-ted/graph-ted-db/blob/main/CONTRIBUTING.md).

## Related

graph-ted-db is the local store for the graph-ted toolkit ([graph-ted.com](https://graph-ted.com/)). This repository is the database library only.
