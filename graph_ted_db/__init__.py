"""Local property-graph storage for Python: open a folder and query it in-process (import graph_ted_db)."""

from graph_ted_db.engine import CypherError
from graph_ted_db.store import (
    EdgeRecord,
    GraphStore,
    NodeRecord,
    Tombstone,
    VectorRecord,
    init_graph,
)
from graph_ted_db.store.meta import MetaEntry, MetaPathError

__version__ = "0.1.0"
__all__ = [
    "CypherError",
    "EdgeRecord",
    "GraphStore",
    "MetaEntry",
    "MetaPathError",
    "NodeRecord",
    "Tombstone",
    "VectorRecord",
    "__version__",
    "init_graph",
]
