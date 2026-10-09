"""README quick start, run against an installed graph-ted-db (release smoke test)."""
import tempfile, os
from graph_ted_db import GraphStore, init_graph

d = os.path.join(tempfile.mkdtemp(), "my-graph")
init_graph(d, name="demo", exist_ok=True)
g = GraphStore.open(d)
alice = g.make_node(labels=["Person"], props={"name": "Alice"})
bob = g.make_node(labels=["Person"], props={"name": "Bob"})
g.make_edge(type="KNOWS", from_id=alice.id, to_id=bob.id)
assert g.get_node(alice.id).props["name"] == "Alice"
print("quickstart OK")
