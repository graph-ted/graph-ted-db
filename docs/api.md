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
| `CypherError` | Invalid or unsupported Cypher |
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

## CypherError

::: graph_ted_db.CypherError
    options:
      show_root_heading: false
      show_if_no_docstring: true
