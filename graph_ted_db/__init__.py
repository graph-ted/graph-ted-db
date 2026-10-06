"""SQLite for graphs: local file-based graph store (import graph_ted_db)."""

from graph_ted_db.engine import CypherError
from graph_ted_db.store import GraphStore, init_graph

__version__ = "0.1.0"
__all__ = ["__version__", "CypherError", "GraphStore", "init_graph"]
