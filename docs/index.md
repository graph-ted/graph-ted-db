<p><img class="gt-hero" alt="graph-ted-db" src="assets/logo-hero.png" width="340" height="116"></p>

# graph-ted-db

**Local property-graph storage for Python.**

Your graph lives in a folder on disk. Open it from your process, read and write nodes and edges, and keep the network off unless you choose otherwise.

**Package** `graph-ted-db` · **Import** `graph_ted_db` · **License** MIT

[Get started](getting-started.md){ .md-button .md-button--primary }
[API overview](overview.md){ .md-button }

## What you get

- A **folder as the database** — plain files you can back up and sync
- An **in-process Python API** — no server for the default path
- An optional **Cypher subset** for pattern queries
- Optional **localhost HTTP** when another process needs the store

## Install

```bash
pip install graph-ted-db
```

Requires Python 3.10+. Until the package is on PyPI, install from a git checkout: `pip install -e .`

## Quick example

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
g = GraphStore.open("./my-graph")

alice = g.make_node(labels=["Person"], props={"name": "Alice"})
bob = g.make_node(labels=["Person"], props={"name": "Bob"})
g.make_edge(type="KNOWS", from_id=alice.id, to_id=bob.id)

print(g.get_node(alice.id).props["name"])
```

## When it fits

Prototypes, local tools, and small trusted groups that want a property graph beside the app.

Prefer a server graph database when you need multi-tenant hosting, rich remote authorization, or a full enterprise Cypher surface on day one.

## Security in one line

The library does not encrypt files on disk. Strength follows your storage and network choices — [read more](security.md).
