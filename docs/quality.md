# Quality gate

CI runs `pytest` on every pull request (`.github/workflows/ci.yml`). A change that drops one of these checks fails that job.

Relationship property filters in `MATCH` (inline maps and `WHERE`) must be honored. Delete-by-filter (`DELETE` / `DETACH DELETE` scoped by node or relationship properties) must touch only the matching records. The regression tests are `tests/test_rel_property_filter.py`, collected by the normal pytest run. They cover two groups of edges, a missing group, anonymous maps, `OPTIONAL MATCH`, `MERGE`, relationship `DELETE`, and node `DETACH DELETE` / `DELETE`.
