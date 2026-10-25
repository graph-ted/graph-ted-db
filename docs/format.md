# graph-ted-db on-disk format

**Format version:** 4 (sealed bundles). Version 3, version 2, and version 1 folders are read. A writer folds its own older files into one bundle on open and bumps `graph.json` when it registers; see [Upgrading from v1](#upgrading-from-v1).

This is the on-disk format of **graph-ted-db**, local property-graph storage for Python and the database component of the **graph-ted** kit. A file-sync client may copy, delay, or fork these files. Readers must treat the folder as eventually consistent and merge by record, not by whole file.

The graph is a folder of ordinary files. No database server is required to open it. The bytes in that folder are this format.

Derived indexes (catalog, inverted fulltext, unpacked vectors, adjacency cache) are **not** part of this format. They live outside the graph folder (or in a no-sync sidecar) and must be reconstructable from what is specified here.

There is no separate database engine file in the graph folder, and no lock files that sync.

## Directory layout

One folder is one graph.

```
<graph>/
  graph.json                         # required
  nodes/
    <writer>.000001.jsonl            # one immutable bundle; every shard in that publish
    <writer>.000002.jsonl
  edges/
    <writer>.000001.jsonl
  vectors/
    <property>/
      <writer>.000001.jsonl
  meta/
    labels.json                      # optional hint; rebuildable
    deleted.<writer>.000001.jsonl    # this writer's tombstones; synced
    writers/
      <writer>.json                  # one registration file per writer
```

The sequence is six digits, starting at `000001`, and it counts publishes of that directory, not shards. A bundle name is `<writer>.<seq>.jsonl`. A version 3 name is `<stem>.<writer>.<seq>.jsonl` (one shard). A version 2 name has no sequence (`<stem>.<writer>.jsonl`). Version 1 files are `<stem>.jsonl` with no writer id. Readers union all of these, plus conflict copies.

The growing log is **not** in this folder. It lives in local app data (`active/<directory>/records.jsonl`), next to the WAL. A sync client only ever sees a bundle after that bundle has been sealed. See [Sealed bundles](#sealed-bundles).

## Writers

Every device (more exactly, every app-data directory) that writes to a graph has a **writer id**: `w` followed by 16 random lowercase hex digits, for example `w3f9c0a17be42d851`. It is generated with a cryptographic random source and is never derived from a user, OS, or host name. It is stored only in local app data (`<app-data>/graph-ted-db/<graph-id>/writer.json`), never in the graph folder. If that file is lost, the device creates a **new** id and never reuses an old one; the old id's files simply become another writer's files.

A writer **only ever creates files whose name carries its own id**. No file in `nodes/`, `edges/`, `vectors/` or the tombstones has two writers, so a sync client never has to choose between two versions of the same file, and adding different records on several devices at once cannot lose data. Processes on one device share the device's writer id and are serialized by the local `LOCK`. A sealed segment is never appended; the next sequence number is chosen under that lock.

On its first write (or when it seals a legacy file), each writer creates `meta/writers/<writer>.json` once (a device that only reads never writes anything to the folder):

```json
{"writer": "w3f9c0a17be42d851", "format_version": 4, "created_at": "2026-10-09T18:00:00.000000Z"}
```

The writers list is the set of these files. A writer file whose registration has not arrived yet is reported by `doctor` (usually the sync is still running).

Do **not** pre-create shard files. Empty files still count against a sync service's file-count limits. A segment appears only when a non-empty log is sealed.

## `graph.json`

UTF-8 JSON object.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `format` | string | yes | Always `"graph-ted-db"` |
| `format_version` | int | yes | `4` |
| `layout` | string | no | `"bundles"` in v4. `"segments"` is v3. `"per-writer"` is v2 |
| `id` | string | yes | Graph UUID (lowercase, hyphenated) |
| `name` | string | yes | Human name; not unique |
| `created_at` | string | yes | UTC timestamp |
| `shard_fanout` | int | yes | `256` in v1 |

Unknown fields must be ignored by readers and preserved by writers that rewrite the file.

Example:

```json
{
  "format": "graph-ted-db",
  "format_version": 4,
  "layout": "bundles",
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "name": "team-memory",
  "created_at": "2026-08-25T15:04:05.000000Z",
  "shard_fanout": 256
}
```

Opening a folder whose `format` is not `graph-ted-db`, or whose `format_version` is greater than the process understands, is an error. A lower version is upgraded when this writer registers (see [Upgrading from v1](#upgrading-from-v1)).

## Identifiers

- Record `id` values are UUID strings: lowercase, 8-4-4-4-12 with hyphens.
- Shard assignment uses the **first byte** of the UUID (the first two hex characters of the hyphenated form).
- `shard_fanout` is 256, so shard stems are `00` … `ff` (lowercase hex). A v4 bundle does not put the stem in the file name: one file holds every shard sealed in that publish, for example `w3f9c0a17be42d851.000001.jsonl`. A v3 file name is `<stem>.<writer>.<seq>.jsonl`, for example `55.w3f9c0a17be42d851.000001.jsonl`. Tombstones use the stem `deleted` (`deleted.<writer>.<seq>.jsonl`) because that directory has only one stem.
- Example: `550e8400-e29b-41d4-a716-446655440000` → shard `55`. The shard still decides which v1 or v3 file a record lives in. A v4 reader looks for that stem's files and also scans the directory's bundles once.

## Timestamps

All `updated_at` / `created_at` values are UTC ISO-8601 with a `Z` suffix, `YYYY-MM-DDTHH:MM:SS` plus optional fractional seconds up to microseconds.

```
2026-08-25T15:04:05Z
2026-08-25T15:04:05.123Z
2026-08-25T15:04:05.123456Z
```

Compare as instants, not as strings. Writers should emit zero-padded microseconds (`%Y-%m-%dT%H:%M:%S.%fZ`).

## JSONL records

Each record file is UTF-8 JSONL: one JSON object per line, newline-terminated. No UTF-8 BOM. Readers ignore blank lines.

A directory may contain **multiple versions of the same id**, across bundles, older per-shard files, and writers. The live version is selected by the LWW rule below (after applying tombstones).

Lines that are not valid JSON objects are skipped and should be reported by `graph-ted-db doctor`.

A hard halt can leave the **local active log** without a trailing newline. An unterminated last line is never data: readers skip it. The owner repairs that local file (truncate back to the last newline) on open, on `doctor`, and before a seal. A **sealed bundle or segment is never modified**, even by its owner: an unterminated tail there is skipped and reported (`sealed_torn`, and `foreign_torn` when the file is not this writer's). A version 2 file is repaired only by its owner, and only as part of folding it. A version 1 shared file is never modified. In a synced folder a missing newline usually means the sync client has not finished delivering the file.

`compact()` republishes this writer's own winning lines into one new bundle (one new tombstone segment for `meta/`) and then deletes that writer's older bundles, per-shard segments, and version 2 files in the directory. It does not copy another writer's bytes and it does not delete version 1 files. It refuses while conflict copies are present (`SharedStoreError`), because deleting a forked file can drop records. Automatic compaction runs when one writer has more than `compact_after` sealed files in one directory (default 4; `0` disables it). `compact()` still runs when asked.

A transaction that writes several records appends its lines to the local active log and then flush+fsyncs each active file once. A local write-ahead record is fsynced before those appends and removed only after the active-log fsync. If the process dies before that record is durable, the transaction is absent. If it dies after the record is durable but before the active-log fsync finishes, the next open replays the record and then seals it. An autocommit statement that writes one record fsyncs that line in the active log and does not write a separate commit record. A torn final line is not returned as a record.

## Sealed bundles

Record bytes are appended only in one local active log per directory:

```
<app-data>/graph-ted-db/<graph-id>/active/nodes/records.jsonl
<app-data>/graph-ted-db/<graph-id>/active/edges/records.jsonl
<app-data>/graph-ted-db/<graph-id>/active/vectors/<property>/records.jsonl
<app-data>/graph-ted-db/<graph-id>/active/meta/records.jsonl
<app-data>/graph-ted-db/<graph-id>/stage/          # temporary files for a seal
```

A leftover `active/.../<stem>.jsonl` from an older process is still read and included in the next seal. New writes go to `records.jsonl`.

A seal concatenates every non-empty active log in that directory into **one** immutable file and then truncates the logs:

1. Write the bytes to a file in `stage/` (outside the synced tree) and fsync them.
2. `os.replace` that file onto `nodes/<writer>.<seq>.jsonl` (or the matching directory). Tombstones use `meta/deleted.<writer>.<seq>.jsonl`. On the same filesystem the name appears only when the file is complete, and the file never changes afterwards.
3. If the stage directory and the graph folder are on different filesystems (`EXDEV`), link an unnamed file in the destination directory (`O_TMPFILE`) or, where that is unavailable, write once to `<name>.<pid>.<random>.tmp` in the destination directory, fsync, and rename. Readers ignore `*.tmp`. The destination name is never appended.
4. Truncate and fsync the active logs. If the process dies between the rename and the truncate, the next open sees a bundle with the same bytes and only truncates the logs.

Put the app-data directory on the same volume as the graph folder when you can, so the normal path is the atomic rename.

**Publish rule.** Configurable with `PublishPolicy` (`GraphStore.open(..., publish=...)`) or the environment variables below. `0` disables that one trigger. The size, age, and idle checks are totals for the directory, not for one shard. Defaults:

| Trigger | Default | Environment | Effect |
|---|---|---|---|
| size | 1 MiB (`1048576`) | `GRAPH_TED_DB_PUBLISH_BYTES` | seal when the directory's active logs reach this many bytes |
| interval | 2 seconds | `GRAPH_TED_DB_PUBLISH_INTERVAL` | while writes keep arriving, seal at least this often |
| idle | 0.5 seconds | `GRAPH_TED_DB_PUBLISH_IDLE` | seal this long after the last write in that directory |
| compact | 4 bundles | `GRAPH_TED_DB_COMPACT_AFTER` | auto-compact a directory after this many of this writer's sealed files |

`GraphStore.publish()` and `GraphStore.close()` seal immediately. A normal process exit does too. A `SIGKILL` leaves the bytes in the active log; the next `open` seals them, including anything the WAL replayed.

**Visibility lag**, not counting the sync client:

- while writing continuously, a record is in the synced folder within `max_interval` (or sooner, when the directory's `max_bytes` is hit);
- after the last write, within `idle`;
- immediately on `publish()` / `close()` / process exit;
- after a crash, on the next `open`.

Other devices see the record only after their sync client copies the new bundle. On this device the record is durable when the active log (or the WAL, for a multi-record transaction) has been fsynced, which is before the seal. This device reads its own active log, so it does not wait.

**Costs.**

- **Lag.** Another machine is behind by the publish lag plus the sync client's lag. A check that copies only the graph folder, and not the writer's app-data directory, sees sealed bundles only. The lag is the same as a per-shard seal: the triggers did not get coarser. What changed is that one seal writes one file, however many shards the batch touched.
- **Read.** Opening the store and a full scan read each bundle once, plus any legacy per-stem files. A point read that misses the local index scans those same bundles and keeps lines with the requested id. After open, the local index answers `get_node` without listing the directory. The index is derived and is not synced.
- **File count.** One new file per publish per directory that received writes (`nodes/`, `edges/`, each vector property, and tombstones). A publish that touches all 256 node shards creates one node file. `compact_after` (default 4) is how many of this writer's files that directory keeps. The count does not grow with the shard fanout or with the number of writes: compaction folds the extras back to one file. A local run of 12,000 node writes, touching every shard, created 9 node files and left 4.
- **Compaction.** When a directory has more than `compact_after` files from this writer, the writer publishes one new bundle of its own last-write-wins winners and deletes its older bundles, per-shard segments, and version 2 files in that directory. That rewrite is proportional to this writer's records in the directory, and it runs every `compact_after` publishes (or when `compact()` is called). Another writer's lines are considered so a dominated line of ours can be dropped, but they are not copied. Until both the new file and the deletes have synced, another machine may briefly see the records twice or, for a moment, not yet see the new bundle. Duplicate lines are safe under last-write-wins. Version 1 files are never deleted: they have no single owner.

Sealing one file per shard would keep the same immutability guarantee and the same lag, and would still let one writer approach `256 × compact_after` files (about four thousand) because compaction was per shard. A sync client then lists and uploads thousands of tiny files. Compacting on every publish (`compact_after` of 1) would also bound the count, and would rewrite the whole history on every seal. Four files is the default so a busy directory stays a handful of files and the rewrite happens only every fifth publish.

**Migration.** On `open` and `doctor`, this writer copies each version 2 file it owns (`<stem>.<writer>.jsonl`, no sequence) and each version 3 per-shard segment it owns into one new bundle and deletes the old names only after the bundle is durable. The copy keeps the bytes, including a dangling edge; `compact()` is what drops dominated lines. If a bundle already holds the same bytes, the old names are deleted and nothing new is written. Tombstones are folded into `deleted.<writer>.<seq>.jsonl`. Another writer's files are read and left in place (`legacy_v2_files` lists version 2 names). `doctor` lists files this process folded (`migrated`) and sealed files with a torn tail (`sealed_torn`).

A crash between the seal and the delete leaves both names. Readers union them; the duplicate lines pick one winner. There is no manifest to update, because a manifest in the synced folder would be a file that changes.

`graph.json`, `labels.json`, and `meta/writers/<writer>.json` are still replaced as whole files (write a temporary file, then rename). They change only when the store is created, a writer registers, the format is bumped, or labels are rebuilt. They are not the files a busy writer appends on every record. Their temporary names end in `.tmp` and are not records.

## Node record

Path: `nodes/<writer>.<seq>.jsonl` (v4). Older files `nodes/<shard>.<writer>.<seq>.jsonl` and `nodes/<shard>.<writer>.jsonl` are still read.

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "v": 1,
  "updated_at": "2026-08-25T15:04:05.000000Z",
  "updated_by": "",
  "writer": "w3f9c0a17be42d851",
  "counter": 0,
  "labels": ["Entity"],
  "props": {
    "name": "Kamala Harris",
    "group_id": "workgroup-1",
    "created_at": "2026-08-25T15:04:05.000000Z"
  }
}
```

| Field | Type | Required |
|---|---|---|
| `id` | UUID string | yes |
| `v` | int | yes (record schema; `1`) |
| `updated_at` | timestamp | yes |
| `updated_by` | string | no (empty if unknown) |
| `writer` | writer id | v2 writers always set it; empty or absent in v1 records |
| `counter` | int ≥ 0 | v2 (hybrid logical clock counter; `0` if absent) |
| `labels` | array of strings | yes (may be empty) |
| `props` | object | yes |

`props` must not contain embedding arrays. Embeddings live under `vectors/`. Property values are JSON: string, number, boolean, null, or arrays of those. Nested objects are allowed but not queried by the v1 openCypher subset.

## Edge record

Path: `edges/<writer>.<seq>.jsonl` (v4). The edge id still selects the shard of a v1 or v3 file, not the from/to nodes.

```json
{
  "id": "1b4e28ba-2fa1-11d2-883f-b9a761bde3fb",
  "v": 1,
  "updated_at": "2026-08-25T15:04:05.000000Z",
  "updated_by": "alice",
  "type": "RELATES_TO",
  "from": "550e8400-e29b-41d4-a716-446655440000",
  "to": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
  "props": {}
}
```

| Field | Type | Required |
|---|---|---|
| `id` | UUID string | yes |
| `v` | int | yes |
| `updated_at` | timestamp | yes |
| `updated_by` | string | no |
| `type` | string | yes (relationship type) |
| `from` | UUID string | yes (source node id) |
| `to` | UUID string | yes (target node id) |
| `props` | object | yes |

## Vector record

Path: `vectors/<property>/<writer>.<seq>.jsonl` (v4). The subject id (the node or edge that owns the embedding) still selects the shard of a v1 or v3 file. `<property>` is `[a-z][a-z0-9_]*` (examples: `name_embedding`, `fact_embedding`).

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "v": 1,
  "updated_at": "2026-08-25T15:04:05.000000Z",
  "updated_by": "alice",
  "dim": 1024,
  "dtype": "f32le",
  "vec": "<base64>"
}
```

`vec` is standard Base64 of `dim` IEEE-754 **little-endian float32** values (`dim * 4` bytes). JSON arrays of floats are invalid.

A missing vector shard is allowed: search that needs it degrades (no vector hits) and must not crash.

## Tombstones

Path: `meta/deleted.<writer>.<seq>.jsonl`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "v": 1,
  "kind": "node",
  "updated_at": "2026-08-25T16:00:00.000000Z",
  "updated_by": "bob"
}
```

`kind` is `node`, `edge`, or `vector`. For `vector`, include `"property": "name_embedding"`.

A tombstone dominates a live record with the same id (and property, for vectors) iff the tombstone **wins LWW** against that record. A later live record with a newer `updated_at` resurrects the id.

**Retention.** Tombstones are kept forever in this version. `compact` keeps this writer's winning tombstone per id and publishes it as a new segment; it does not delete another writer's tombstones. So a device that was offline for a long time and comes back with old versions cannot bring deleted records back: its old versions are older than the tombstone and lose. Only a version stamped **after** the tombstone wins, which is an edit, not a resurrection by accident. A future cleanup must not drop a tombstone until every registered writer is known to have seen it.

## Last-write-wins

Compared per **record id** (and vector property), not per file, and **not per property inside `props`**.

LWW only answers: when two complete versions of the **same** node, edge, or vector exist, which one is live? It is the concurrent-edit rule for a shared folder. It must **not** invent a third object, splice fields, or leave the graph half-applied.

1. Union every line for that id from that stem's files (v1, v2, v3, and their conflict copies) and from every bundle in the directory, including a bundle's conflict copies. Bundles are scanned once per pass, not once per stem.
2. Apply tombstones as candidate versions with `kind` set.
3. Pick **one whole candidate**. The winner is always a record some writer actually wrote (or a tombstone they wrote). Fields from the loser are not merged in.

Each version carries a **hybrid logical clock** `(updated_at, counter, writer)`. A writer never stamps a new version earlier than the greatest `(updated_at, counter)` it has read from the store or written itself: it uses `max(wall clock, greatest seen)`, and if that equals the greatest seen time it increments `counter`. So an edit made after seeing a record always wins over that record, even when this device's clock is behind. Records written as-is with an explicit older `updated_at` keep it (and lose).

Deterministic order, compared as a tuple (greater wins):

1. `updated_at` (as an instant).
2. `counter`.
3. `writer` (string comparison; v1 records have the empty writer).
4. A tombstone beats a live record.
5. Greater `v`.
6. Lexicographically greater JSON line (last resort).

Every device computes the same order, so after sync all devices pick the same winner.

### What LWW may lose

If Alice changes `props.name` and Bob changes `props.summary` on the same node without seeing each other, one complete snapshot wins and the other edit is dropped. Attribute-level merge (field clocks / CRDT of `props`) is **not** v1; whole-record LWW is the resolution a JSONL folder can support without corrupting objects.

### What LWW must not do

- Must not concatenate or splice two JSON objects into one malformed or mixed record. Torn-line repair (above) exists so a crash cannot fuse two writes.
- Must not produce a live node whose `props`/`labels` were taken half from Alice and half from Bob.
- Must not leave a **live edge whose endpoints are not live nodes**. `delete_node` tombstones incident edges (DETACH). Reads skip dangling edges. `doctor` reports any that remain; `doctor --fix` tombstones them. In a synced folder an edge can arrive before its nodes, and a tombstone is permanent on every device, so run `--fix` only after sync has finished. Compact omits dangling winners from the canonical shard.

Limits: the clock only corrects skew between versions a writer has **seen**. Two devices that edit the same record without having synced still order by their own wall clocks, so a device whose clock runs fast wins those concurrent edits. A device with a clock far in the future pulls everyone's clock forward to its time. Application-level history (for example an episode log) is the user-visible change log; this rule only decides the materialised record.

## Sync-client conflict copies

Sync clients fork a file when both sides edited it. Names vary; v1 treats an extra file in a shard directory as a conflict sibling of the canonical `NN.jsonl` if:

- it is not exactly `NN.jsonl`,
- it ends in `.jsonl`, **or** it is `NN.jsonl` followed by a conflict suffix (`.conflict1`, `.<name>-conflict2`, `..path1`, `..path2`), which is how rclone bisync renames both sides of a conflict, and
- the stem starts with the shard hex (`00`, `ff`, …) or contains the canonical filename stem.

The same rule applies to `meta/deleted.jsonl`, so forked tombstones still apply. A file whose name is `<stem>.<writer>.jsonl` or `<stem>.<writer>.<seq>.jsonl` is part of the stem (it is not a conflict copy). Conflict copies of those names (`….jsonl.conflict1`) are ingested too. Because each of those files has one writer, a sync client should not need to fork them; the rule stays for v1 files and odd clients.

Examples that must be ingested and union-merged:

```
nodes/00.jsonl
nodes/00-DESKTOP-NAME-conflict-2026-08-25.jsonl
nodes/00 (conflicted copy).jsonl
nodes/00-conflict-copy.jsonl
nodes/00.jsonl.conflict1
nodes/00.jsonl..path2
meta/deleted.jsonl.conflict1
```

rclone bisync removes `NN.jsonl` when it renames both sides, so a shard can consist of conflict copies only until the next write recreates `NN.jsonl`. Transfers still in progress (`*.partial`) and temporary files (`*.tmp`) are never read.

After a successful merge, a writer **may** append the union into `NN.jsonl` and delete conflict copies. Deleting is optional: leaving them is safe; they remain part of the union. Compaction should wait until the sync client is idle enough that the delete will not resurrect a stale copy.

**Cloud-only files (OneDrive Files On-Demand and similar).** A sync client may keep only a placeholder on disk and download the bytes when a file is read. If that download fails, the store opens with records missing. graph-ted-db reports such files: on Windows it checks the placeholder attributes (`RECALL_ON_DATA_ACCESS`, `RECALL_ON_OPEN`, `OFFLINE`); everywhere it reports zero-byte record files. Both show in `doctor`, in `GraphStore.problems()`, in `graph-ted-db info --check`, in `/health`, and as a warning on open. Set the graph folder to **Always keep on this device** (OneDrive) or the equivalent offline/"make available offline" option of your client.

Never put a process lock file in the graph folder. Local `LOCK` lives in unsynced app data.

## `meta/labels.json`

Optional, rebuildable hint:

```json
{
  "node_labels": ["Entity", "Episodic"],
  "relationship_types": ["RELATES_TO", "MENTIONS"],
  "vector_properties": ["name_embedding", "fact_embedding"]
}
```

If absent or stale, scan shards. Do not use this file as an access-control list.

## Local derived state (not this format)

Suggested location: platform app data / cache dir `graph-ted-db/<graph-id>/`.

| File | Role |
|---|---|
| `catalog.jsonl` | id → shard, labels, types |
| `fulltext/` | inverted index for BM25 |
| `vectors.bin` | unpacked f32 cache |
| `adj.jsonl` | adjacency |
| `LOCK` | cross-process lock for this machine |
| `writer.json` | this device's random writer id (**not** rebuildable: losing it starts a new id) |
| `wal/` | local write-ahead record for an in-flight transaction |
| `active/` | this writer's growing record log, sealed into the graph folder by the publish rule |
| `stage/` | temporary files for a seal; never synced |

These must not be placed in the synced graph folder. Losing `active/` before a seal loses records that were durable on this device but not yet published. `writer.json` is not rebuildable: losing it starts a new id. The index and WAL are rebuilt or replayed from the graph folder and `active/`.

## What this format is not

- Not a single-file database.
- Not one file per node/edge (sync services limit file counts).
- Not CRDT merge of property maps. Whole-record LWW: one complete object wins; concurrent field-level edits on the same id can lose the non-winning write, they must not mix into a corrupt object.
- Not encrypted at rest by the library. Security is as strong as your storage and network (encrypted volumes and an airgap can be very strong; synced or shared folders are not). The folder ACL / disk encryption / network exposure you choose is the security boundary — see [Security](security.md).

## Upgrading from v1

A v1 folder has shared files (`nodes/NN.jsonl`, `meta/deleted.jsonl`) that any writer appended to. This library reads them as part of each stem, never writes them again, and never modifies them (they are "v1 shared files" in `doctor`).

A v2 folder has mutable `<stem>.<writer>.jsonl` files. A v3 folder has immutable `<stem>.<writer>.<seq>.jsonl` files, one per shard per publish. This library reads every writer's files. On open it folds **this** writer's v2 and v3 files into one bundle and deletes the old names only after that bundle is durable. Other writers' files stay until those writers open the folder (`legacy_v2_files` lists the v2 names). While both names exist, readers union the lines.

On the first write, or when this writer folds a legacy file, `graph.json` is set to `format_version` `4` and `layout` `"bundles"`. An older library refuses to open that folder, which is intended: a v1 or v2 writer would append to a file a sync client can observe mid-write, and a v3 writer would seal a new file per shard. A reader that understands v4 can open a folder that is only partly migrated.

## App metadata files (`meta/<namespace>/`)

Applications can keep small JSON documents next to the graph, for example
versioned ontology catalogs, with `GraphStore.meta_put(directory, name, data)`
and `GraphStore.meta_list(directory)`. They follow the per-writer rule:

```text
meta/ontology/default/v1.<writer-id>.json
meta/ontology/default/v2.<writer-id-a>.json   # two devices published v2 from v1:
meta/ontology/default/v2.<writer-id-b>.json   # both files are kept
meta/ontology/default/head.<writer-id-a>.json
```

- A device only creates or replaces its own `<name>.<writer-id>.json`, so a sync
  client never merges two devices' edits of one file. Deciding what to do with
  two `v2` files (for example flagging a conflict) is the application's job.
- Writes are atomic (temporary file, then rename) and end with a newline. An
  existing file is not replaced unless `replace_existing=True` (pointers such
  as `head`), so version histories are append-only.
- `writers/`, `labels.json` and `deleted*` under `meta/` belong to the store and
  cannot be used. Directory and file names are plain relative segments
  (letters, digits, `.`, `_`, `-`).
- `meta_list` returns every writer's files. A file that cannot be read is
  returned with `problem` set (`cloud-only`, `empty`, `unterminated`,
  `invalid-json`, `conflict-copy`, `tmp`) and is never skipped silently or
  modified. `doctor` and `problems()` report the same files (`meta_problems`).
