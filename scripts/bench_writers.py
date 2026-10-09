"""Per-writer layout benchmark: 1, 2 and 4 writers on one folder (no sync).

Usage: python scripts/bench_writers.py [records]
"""

import json, random, shutil, sys, tempfile, time
from pathlib import Path
from graph_ted_db import GraphStore, init_graph

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
EDITS = 2
out = {}
base = Path(tempfile.mkdtemp(prefix="gtdb-bench-"))
shutil.rmtree(base, ignore_errors=True)
for w in (1, 2, 4):
    root = base / f"w{w}" / "g"
    init_graph(root, name="bench")
    stores = [GraphStore.open(root, data_dir=base / f"w{w}" / f"d{k}") for k in range(w)]
    random.seed(1)
    ids = []
    t0 = time.perf_counter()
    for i in range(N):
        s = stores[i % w]
        ids.append(s.make_node(labels=["E"], props={"i": i, "name": f"n{i}"}).id)
    for e in range(EDITS):
        for i, rid in enumerate(ids):
            stores[random.randrange(w)].make_node(
                record_id=rid, labels=["E"], props={"i": i, "name": f"n{i}", "rev": e}
            )
    write_s = time.perf_counter() - t0
    files = [p for p in root.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    opens = []
    for k in range(3):
        t = time.perf_counter()
        g = GraphStore.open(root, data_dir=base / f"w{w}" / f"fresh{k}")
        opens.append(time.perf_counter() - t)
    qs = []
    for _ in range(5):
        t = time.perf_counter()
        g.execute("MATCH (n:E) WHERE n.i = 12345 RETURN n.name AS name")
        qs.append(time.perf_counter() - t)
    gets = []
    for _ in range(5):
        t = time.perf_counter()
        g.get_node(ids[777])
        gets.append(time.perf_counter() - t)
    out[w] = dict(
        records=N,
        versions=N * (1 + EDITS),
        write_s=round(write_s, 2),
        open_s=round(min(opens), 3),
        match_ms=round(min(qs) * 1000, 2),
        get_ms=round(min(gets) * 1000, 2),
        files=len(files),
        size_mb=round(size / 1e6, 2),
        live=len(list(g.iter_nodes())),
    )
    print(w, out[w], flush=True)
print(json.dumps(out, indent=2))
shutil.rmtree(base, ignore_errors=True)
