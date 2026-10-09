# API reference

Public names exported by `graph_ted_db`.

!!! note
    Import these from `graph_ted_db`. Other modules in the package are internal and can change without a version note.

```python
from graph_ted_db import GraphStore, init_graph

init_graph("./my-graph", name="demo", exist_ok=True)
store = GraphStore.open("./my-graph")
```

| Name | Role |
| --- | --- |
| `GraphStore` | Open a graph folder and read or write it |
| `init_graph` | Create an empty graph folder |
| `NodeRecord` | One node version: returned by `make_node` / `get_node` / `iter_nodes`, accepted by `put_node` |
| `EdgeRecord` | One edge version: returned by `make_edge` / `get_edge` / `iter_edges`, accepted by `put_edge` |
| `VectorRecord` | An embedding for a node or edge: `get_vector` / `put_vector` |
| `Tombstone` | Deletion marker returned by `delete_node` / `delete_edge` / `delete_vector` |
| `CypherError` | Invalid query, or syntax outside the supported openCypher subset |
| `__version__` | Package version string (`graph_ted_db.__version__`) |

## GraphStore

::: graph_ted_db.GraphStore
    options:
      show_root_heading: false
      show_if_no_docstring: true
      inherited_members: true
      show_bases: false

## init_graph

::: graph_ted_db.init_graph
    options:
      show_root_heading: false

## NodeRecord

::: graph_ted_db.NodeRecord
    options:
      show_root_heading: false
      members: false

## EdgeRecord

::: graph_ted_db.EdgeRecord
    options:
      show_root_heading: false
      members: false

## VectorRecord

::: graph_ted_db.VectorRecord
    options:
      show_root_heading: false
      members: [from_floats, floats]

## Tombstone

::: graph_ted_db.Tombstone
    options:
      show_root_heading: false
      members: false

## CypherError

::: graph_ted_db.CypherError
    options:
      show_root_heading: false
      show_if_no_docstring: true
