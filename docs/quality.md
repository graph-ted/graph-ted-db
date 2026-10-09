# Quality gate

CI runs `pytest` on every pull request (`.github/workflows/ci.yml`): Python 3.10 to 3.14 on Linux, and Python 3.13 on macOS and Windows. The crash tests below run on Linux and macOS; their harness needs `SIGKILL`, which Windows lacks.

Relationship property filters in `MATCH` (inline maps and `WHERE`) must be honored. Delete-by-filter (`DELETE` / `DETACH DELETE` scoped by node or relationship properties) must touch only the matching records. The regression tests are `tests/test_rel_property_filter.py`, collected by the normal pytest run. They cover two groups of edges, a missing group, anonymous maps, `OPTIONAL MATCH`, `MERGE`, relationship `DELETE`, and node `DETACH DELETE` / `DELETE`.

A crash loses at most the in-flight transaction: every committed transaction is fully present after reopen, and the in-flight one is entirely present or entirely absent. A one-record autocommit fsyncs that line and does not write a separate commit record. A torn final line in a shard or tombstone file is skipped (and repaired only in the writer's own files) and is not returned as a committed record. The tests are `tests/test_crash_durability.py`.

## Several writers (per-writer layout)

`scripts/bench_writers.py` writes 20,000 nodes and edits each twice (60,000 versions), spread round-robin and at random over 1, 2 or 4 writers in one folder, then opens it from a fresh app-data directory. Single run on an 8-vCPU Linux VM (shared with other jobs, so expect some noise), Python 3.13; open is the best of 3, queries the best of 5.

| Writers | Open | `MATCH … WHERE n.i = …` | `get_node` | Files | Size |
|---|---|---|---|---|---|
| 1 | 2.55 s | 128 ms | 2.8 ms | 259 | 12.4 MB |
| 2 | 2.91 s | 115 ms | 3.4 ms | 516 | 12.4 MB |
| 4 | 2.97 s | 112 ms | 3.7 ms | 1,030 | 12.4 MB |

Size depends on the number of versions, not on the number of writers. The file count grows with writers × shards (up to 256 per writer per directory), and open time grows with the number of files (+14% at 2 writers, +16% at 4). A device that only reads adds no files.
