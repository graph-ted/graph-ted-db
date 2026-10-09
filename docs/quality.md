# Quality gate

CI runs `pytest` on every pull request (`.github/workflows/ci.yml`): Python 3.10 to 3.14 on Linux, and Python 3.13 on macOS and Windows. The crash tests below run on Linux and macOS; their harness needs `SIGKILL`, which Windows lacks.

Relationship property filters in `MATCH` (inline maps and `WHERE`) must be honored. Delete-by-filter (`DELETE` / `DETACH DELETE` scoped by node or relationship properties) must touch only the matching records. The regression tests are `tests/test_rel_property_filter.py`, collected by the normal pytest run. They cover two groups of edges, a missing group, anonymous maps, `OPTIONAL MATCH`, `MERGE`, relationship `DELETE`, and node `DETACH DELETE` / `DELETE`.

A crash loses at most the in-flight transaction: every committed transaction is fully present after reopen, and the in-flight one is entirely present or entirely absent. A one-record autocommit fsyncs that line and does not write a separate commit record. A torn final line in a shard or `meta/deleted.jsonl` is skipped or repaired and is not returned as a committed record. The tests are `tests/test_crash_durability.py`.
