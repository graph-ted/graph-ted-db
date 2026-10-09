# Getting started

## Install

```bash
pip install graph-ted-db
```

Python 3.10+. If the package is not on PyPI yet:

```bash
git clone https://github.com/graph-ted/graph-ted-db.git
cd graph-ted-db
pip install -e .
```

## Create and open a graph

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
g = GraphStore.open("./my-graph")
```

## Nodes and edges

```python
alice = g.make_node(labels=["Person"], props={"name": "Alice"})
bob = g.make_node(labels=["Person"], props={"name": "Bob"})
g.make_edge(type="KNOWS", from_id=alice.id, to_id=bob.id)

assert g.get_node(alice.id).props["name"] == "Alice"
for node in g.iter_nodes():
    print(node.id, node.labels, node.props)
```

## Command line

```bash
graph-ted-db init ./my-graph --name demo
graph-ted-db put-node ./my-graph --label Person --prop name=Alice
graph-ted-db ls-nodes ./my-graph
graph-ted-db info ./my-graph
```

`graphted-db` is an alias for the same CLI.

## Run openCypher queries

```python
print(g.execute("MATCH (n) RETURN n.name AS name"))
```

See [openCypher subset](cypher.md).

## Optional HTTP

```bash
graph-ted-db serve ./my-graph
```

Defaults to `http://127.0.0.1:8099`. See [HTTP](http.md).

## Next

- [Overview](overview.md) — product shape and design
- [On-disk format](format.md) — what the folder contains
- [Security](security.md) — storage and network boundaries
