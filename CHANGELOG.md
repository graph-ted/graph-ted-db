# Changelog

All notable changes to graph-ted-db are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Until 1.0.0, a minor
version may include breaking changes; they are called out under **Changed**.

## [Unreleased]

## [0.1.0]

First public release.

### Security

- Unauthenticated `GET /health` returns only `ok` and the library version.
  The graph name, id, counts and problem counts are on `GET /info`, which
  needs the token when one is set.
- `serve` warns when it listens off loopback, even with a token.

### Added

- `GraphStore` and `init_graph`: a property graph stored in a folder of
  sharded JSONL files, opened and queried in-process with no server.
- Record types exported from `graph_ted_db`: `NodeRecord`, `EdgeRecord`,
  `VectorRecord`, `Tombstone`.
- Nodes, edges and embedding vectors: `make_node` / `make_edge`, `put_*`,
  `get_*`, `iter_nodes` / `iter_edges`, `delete_*` (tombstones).
- Per-record last-write-wins, and reading of sync-tool conflict copies
  (OneDrive- and Dropbox-style names, rclone bisync `.conflictN` / `..pathN`)
  so a graph folder can be shared through a file-sync service. Multiple
  devices can add and edit at the same time; concurrent edits to the same
  record resolve to the latest version.
- On-disk format version 2 (`docs/format.md`): each writer appends only to
  its own files (`<shard>.<writer>.jsonl`, `meta/deleted.<writer>.jsonl`),
  with a random writer id kept in local app data and a registration file in
  `meta/writers/`. Versions are ordered by a hybrid logical clock
  `(updated_at, counter, writer)`. Version 1 folders are read, and upgraded on
  the first write.
- Torn-tail repair touches only this writer's own files; other writers' files
  are never modified, and an unterminated last line is skipped and reported.
  `compact` refuses to run on a shared store (`SharedStoreError`).
- `GraphStore.problems()`, `graph-ted-db info --check`, a `problems` object
  in `/health` (counts only), and a warning on open, for skipped lines,
  other writers' unterminated files, cloud-only placeholders and empty record
  files. `doctor` lists them.
- `graph-ted-db export` / `import` and `GraphStore.export` /
  `import_export`: back up the current state to one JSONL file and restore
  it into a new graph. Backup guidance in `docs/backup.md`.
- openCypher: `count(*)`, and unaliased `RETURN` expressions (the column is
  named by its source text, e.g. `b.name`).
- Opening a graph above 250,000 records logs how long it took and points to
  the Graph size docs.
- Crash safety: one flush and fsync per multi-record transaction, replay of
  an interrupted transaction on open, torn-line repair.
- `GraphStore.execute` / `execute_many`: a documented subset of openCypher,
  with `CypherError` (and its source position) for anything outside it.
- NetworkX-style helpers: `add_node`, `add_edge`, `neighbors`, `has_node`,
  `has_edge`, `remove_node`, `remove_edge`, `nodes`, `edges`.
- `graph-ted-db` CLI (alias `graphted-db`): `init`, `info`, node and edge
  CRUD, `compact`, `doctor` (repairs torn lines and reports dangling edges;
  `--fix` tombstones them), `cypher`, `serve`, `--version`.
- `updated_by` (record author) is empty by default and never taken from the
  OS login name. Set it with `--by` / `--updated-by` on CLI writes, the
  `GRAPH_TED_DB_UPDATED_BY` environment variable, or `updated_by=` in Python.
- `graph-ted-db serve`: optional localhost JSON-over-HTTP endpoint
  (`/health`, `/info`, `/cypher`, atomic `statements` batches). Binding
  beyond loopback requires a token. `/health`, the one route open without a
  token, does not include the folder path.
- Optional Graphiti driver (`graph_ted_db.driver.GraphTedDbDriver`), in-process
  or over HTTP.
- Documentation site with `llms.txt` and `llms-full.txt`.

[Unreleased]: https://github.com/graph-ted/graph-ted-db/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/graph-ted/graph-ted-db/releases/tag/v0.1.0
