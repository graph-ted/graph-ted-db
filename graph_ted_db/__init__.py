"""Local property-graph storage for Python: open a folder and query it in-process (import graph_ted_db)."""

from graph_ted_db.engine import CypherError
from graph_ted_db.store import GraphStore, init_graph

__version__ = "0.1.0"
__all__ = ["CypherError", "GraphStore", "__version__", "init_graph"]
