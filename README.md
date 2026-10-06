# graphted-db

Local property-graph storage for Python. Your data lives in a folder on disk; open it from your process, query it in-process, and optionally sync that folder like any other files. No database server to run for the default path.

**Package:** `graphted-db` · **Import:** `graph_ted_db` · **License:** MIT

> Status: the public API and on-disk format below are what we ship. `pip install graphted-db` is the intended install; until the package is on PyPI, use a checkout (`pip install -e .`) or a wheel path.

---

## Install

```bash
pip install graphted-db
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
graphted-db init ./my-graph --name demo
graphted-db put-node ./my-graph --label Person --prop name=Alice
graphted-db ls-nodes ./my-graph
```

## Features

- **Folder = database** — nodes, edges, and embeddings as sharded JSONL; readable, backupable, syncable
- **In-process API** — `put` / `get` / `iter` / `delete` without opening a network port
- **Cypher subset** — `g.execute(...)` for graph-pattern queries ([docs/cypher.md](docs/cypher.md))
- **Optional HTTP** — localhost Cypher endpoint when another process needs the store ([docs/http.md](docs/http.md))
- **Sync-friendly** — last-write-wins per record; conflict-copy shards merged on read
- **NetworkX-style helpers** — `add_node`, `add_edge`, `neighbors`, and related methods on the same `GraphStore` (no algorithm suite)

## When to use it

Good fit for prototypes, local tools, agents, and small trusted groups that want a property graph next to the app.

Choose a server graph database (Neo4j, etc.) when you need multi-tenant hosting, fine-grained remote auth, or a full Cypher/enterprise feature set out of the box.

## Security

Data is stored as ordinary files. The library does **not** encrypt the graph folder. Security matches your environment: disk or volume encryption, OS account permissions, backups, sync tools, and whether anything listens on the network.

- **Airgapped host + encrypted volume** — can be very strong; treat that environment as your boundary.
- **Synced or shared folders** — anyone with access to the folder can read what you stored (including properties).
- **HTTP serve** — defaults to localhost; binding beyond loopback requires an explicit host and a token. That protects the port, not the files on disk.

Choose storage and network to match how sensitive the data is.

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/overview.md](docs/overview.md) | Product overview |
| [docs/format.md](docs/format.md) | On-disk layout |
| [docs/cypher.md](docs/cypher.md) | Supported Cypher |
| [docs/http.md](docs/http.md) | Local HTTP serve |

## Versioning

Pre-1.0 (`0.1.0`). Until 1.0.0, minor versions may include breaking changes; they are noted in release notes. From 1.0.0, breaking changes bump the major version.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Related

graphted-db is the local store used by [graph-ted](https://github.com/graph-ted/graph-ted) standalone. Hosted deployments may use other backends. This repository is the database library only.
