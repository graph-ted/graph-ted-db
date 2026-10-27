# Changelog

All notable changes to graph-ted-db are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Until 1.0.0, a minor
version may include breaking changes; they are called out under **Changed**.

## [Unreleased]

## [0.1.3] - 2026-10-27

### Upgrading to 0.1.3

- No format change. 0.1.3 reads and writes the same folders as 0.1.2, so
  machines on 0.1.2 and 0.1.3 can share a folder.
- Queries that compare vectors of different sizes now fail instead of
  scoring 0.0. If you see that error, re-embed the graph with one model.
  See [Upgrading](docs/upgrading.md).

### Changed

- `vector.similarity.cosine` raises a `CypherError` naming both sizes when
  the two vectors have different dimensions. It used to return 0.0, which
  hid a graph that mixed embedding models.
- Opening an older-format (v2) folder that reads normally is no longer
  reported as a problem. It logs one info line instead of "opened with
  problems … run `graph-ted-db doctor`": "This graph has files in an older
  storage format. Each machine upgrades them when it saves with graph-ted-db
  0.1.2 or newer. Every machine sharing this folder needs 0.1.2 or newer."
  `legacy_v2_files` moved from `GraphStore.problems()` to the new
  `GraphStore.notices()` (also `notices` in `/info`). `info --check` prints
  it on an `info:` line and no longer exits 1 for it alone; `doctor` lists
  it as `info:`. The CLI now prints the library's info lines on stderr.

### Fixed

- `doctor` prints the older-format line once (it was printed on stderr and
  again in the report).

## [0.1.2] - 2026-10-26

### Upgrading to 0.1.2

- Upgrade every machine that shares a graph folder at the same time.
- After 0.1.2 writes to a folder, 0.1.1 and older can't open it. Reading with
  0.1.2 doesn't change the folder; the first write does.
- Check a machine's version with `graph-ted-db --version` or
  `pip show graph-ted-db`.
- An older release refuses the folder without writing to it, exits with
  status 1, and prints:
  `graph-ted-db: graph format_version 4 is newer than this library (2)`.
  See [Upgrading](docs/upgrading.md).

### Fixed

- Process exit does not wait forever on the store lock. If a background
  publish still holds it, the active log stays as it is and the next open
  seals it (the same recovery as after `SIGKILL`).
- Deleting a node or edge tombstones its embeddings in the same transaction
  (`delete_node` / `delete_edge`, and Cypher `DELETE` / `DETACH DELETE`,
  including over `serve`). Vector search does not return that element
  afterwards, and a newer embedding from another writer does not bring it
  back while the element stays deleted. Another element's embeddings are
  left in place. No on-disk format change: the extra lines are the existing
  `kind: "vector"` tombstones.
- Vector similarity over `serve` returns hits. Embeddings written with
  `SET n = …` or `CALL db.create.setNodeVectorProperty` /
  `setRelationshipVectorProperty` are stored under `vectors/` and are read
  back on `n.<property>` (and `properties(n)`), including after the active
  log is sealed into a v4 bundle. Cosine search no longer scores every row
  as null.
- `graph-ted-db serve` can bind its port again as soon as the previous
  process has stopped, while that process's connections are still draining.
  A second live server still cannot listen on the same port.

### Added

- `py.typed` marker (PEP 561): type checkers use graph-ted-db's inline types.
- On-disk format version 4 (`docs/format.md`): record bytes grow in one local
  active log per directory and are published as one immutable bundle per
  publish (`<writer>.<seq>.jsonl`), covering every shard the batch touched.
  A synced folder no longer contains a record file that is still being
  appended, and the file count stays bounded (default at most 4 bundles per
  writer per directory) instead of growing with the shard fanout.
  `PublishPolicy` (and `GRAPH_TED_DB_PUBLISH_BYTES`,
  `GRAPH_TED_DB_PUBLISH_INTERVAL`, `GRAPH_TED_DB_PUBLISH_IDLE`,
  `GRAPH_TED_DB_COMPACT_AFTER`) sets the seal rule. `GraphStore.publish`
  seals immediately. Owned v2 files and v3 per-shard segments are folded into
  a bundle on open; readers union a folder that is only partly migrated.
  `doctor` reports `legacy_v2_files`, `migrated`, and `sealed_torn`.

### Changed

- On-disk format version 4. Upgrade every device that writes to a shared
  folder to 0.1.2: the first 0.1.2 writer folds its own older files into a
  bundle and bumps `graph.json` to `format_version` 4, and 0.1.1 or earlier
  then refuses to open the folder (format version newer than it understands).
  No manual migration step; v1-v3 folders are read and upgraded on open.
- `compact()` republishes this writer's own bundles. Another writer's files
  are not a reason to refuse; conflict copies still are. Automatic compaction
  runs after 4 sealed files in a directory (previously 16 files per shard).
- The file-sync harness treats a bisync critical lockout (exit 7 with a
  `.lst-err` file) as a failure, runs one `--resync`, and keeps a separate
  log per phase. Error lines are not truncated. OneDrive upload URLs and
  query tokens are scrubbed as rclone output is written, so a killed run does
  not leave an access token in the log.

## [0.1.1] - 2026-10-10

### Added

- App metadata files: `GraphStore.meta_put` / `meta_list` keep small JSON documents
  under `meta/<namespace>/` as `<name>.<writer-id>.json` (one file per writer,
  atomic, append-only by default). `doctor` and `problems()` report unreadable
  ones (`meta_problems`) without modifying them. See docs/format.md.

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

[Unreleased]: https://github.com/graph-ted/graph-ted-db/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/graph-ted/graph-ted-db/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/graph-ted/graph-ted-db/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/graph-ted/graph-ted-db/releases/tag/v0.1.0
