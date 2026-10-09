# Changelog

All notable changes to graph-ted-db are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Until 1.0.0, a minor
version may include breaking changes; they are called out under **Changed**.

## [Unreleased]

## [0.1.0]

First public release.

### Added

- `GraphStore` and `init_graph`: a property graph stored in a folder of
  sharded JSONL files, opened and queried in-process with no server.
- Record types exported from `graph_ted_db`: `NodeRecord`, `EdgeRecord`,
  `VectorRecord`, `Tombstone`.
- Nodes, edges and embedding vectors: `make_node` / `make_edge`, `put_*`,
  `get_*`, `iter_nodes` / `iter_edges`, `delete_*` (tombstones).
- Per-record last-write-wins, and reading of sync-tool conflict copies
  (OneDrive- and Dropbox-style names, rclone bisync `.conflictN` / `..pathN`)
  so a graph folder can be shared through a file-sync service, one writer at
  a time.
- Crash safety: one flush and fsync per multi-record transaction, replay of
  an interrupted transaction on open, torn-line repair.
- `GraphStore.execute` / `execute_many`: a documented subset of openCypher,
  with `CypherError` (and its source position) for anything outside it.
- NetworkX-style helpers: `add_node`, `add_edge`, `neighbors`, `has_node`,
  `has_edge`, `remove_node`, `remove_edge`, `nodes`, `edges`.
- `graph-ted-db` CLI (alias `graphted-db`): `init`, `info`, node and edge
  CRUD, `compact`, `doctor` (repairs torn lines and reports dangling edges;
  `--fix` tombstones them), `cypher`, `serve`, `--version`.
- `graph-ted-db serve`: optional localhost JSON-over-HTTP endpoint
  (`/health`, `/info`, `/cypher`, atomic `statements` batches). Binding
  beyond loopback requires a token.
- Optional Graphiti driver (`graph_ted_db.driver.GraphTedDbDriver`), in-process
  or over HTTP.
- Documentation site with `llms.txt` and `llms-full.txt`.

[Unreleased]: https://github.com/graph-ted/graph-ted-db/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/graph-ted/graph-ted-db/releases/tag/v0.1.0
