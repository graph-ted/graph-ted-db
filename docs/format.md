# graph-ted-db on-disk format

**Format version:** 2 (per-writer layout). Version 1 folders are read and upgraded on open; see [Upgrading from v1](#upgrading-from-v1).

This is the on-disk format of **graph-ted-db**, local property-graph storage for Python and the database component of the **graph-ted** kit. A file-sync client may copy, delay, or fork these files. Readers must treat the folder as eventually consistent and merge by record, not by whole file.

The graph is a folder of ordinary files. No database server is required to open it. The bytes in that folder are this format.

Derived indexes (catalog, inverted fulltext, unpacked vectors, adjacency cache) are **not** part of this format. They live outside the graph folder (or in a no-sync sidecar) and must be reconstructable from what is specified here.

There is no separate database engine file in the graph folder, and no lock files that sync.

## Directory layout

One folder is one graph.

```
<graph>/
  graph.json                 # required
  nodes/
    00.<writer>.jsonl        # one file per shard per writer, created on first write
    ff.<writer>.jsonl
  edges/
    00.<writer>.jsonl
  vectors/
    <property>/
      00.<writer>.jsonl
  meta/
    labels.json              # optional hint; rebuildable
    deleted.<writer>.jsonl   # this writer's tombstones; synced
    writers/
      <writer>.json          # one registration file per writer
  logs/                      # optional append-only mutation log; not required to open
```

## Writers

Every device (more exactly, every app-data directory) that writes to a graph has a **writer id**: `w` followed by 16 random lowercase hex digits, for example `w3f9c0a17be42d851`. It is generated with a cryptographic random source and is never derived from a user, OS, or host name. It is stored only in local app data (`<app-data>/graph-ted-db/<graph-id>/writer.json`), never in the graph folder. If that file is lost, the device creates a **new** id and never reuses an old one; the old id's files simply become another writer's files.

A writer **only ever appends to, or rewrites, files whose name carries its own id**. No file in `nodes/`, `edges/`, `vectors/` or the tombstones has two writers, so a sync client never has to choose between two versions of the same file, and adding different records on several devices at once cannot lose data. Processes on one device share the device's writer id and are serialized by the local `LOCK`.

Each writer creates `meta/writers/<writer>.json` once:

```json
{"writer": "w3f9c0a17be42d851", "format_version": 2, "created_at": "2026-10-09T18:00:00.000000Z"}
```

The writers list is the set of these files. A writer file whose registration has not arrived yet is reported by `doctor` (usually the sync is still running).

Do **not** pre-create all 256 shard files. Empty files still count against a sync service's file-count limits. Create a shard file when the first record for that shard is written.

## `graph.json`

UTF-8 JSON object.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `format` | string | yes | Always `"graph-ted-db"` |
| `format_version` | int | yes | `2` |
| `layout` | string | no | `"per-writer"` in v2 |
| `id` | string | yes | Graph UUID (lowercase, hyphenated) |
| `name` | string | yes | Human name; not unique |
| `created_at` | string | yes | UTC timestamp |
| `shard_fanout` | int | yes | `256` in v1 |

Unknown fields must be ignored by readers and preserved by writers that rewrite the file.

Example:

```json
{
  "format": "graph-ted-db",
  "format_version": 2,
  "layout": "per-writer",
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "name": "team-memory",
  "created_at": "2026-08-25T15:04:05.000000Z",
  "shard_fanout": 256
}
```

Opening a folder whose `format` is not `graph-ted-db`, or whose `format_version` is greater than the process understands, is an error. A lower version is upgraded in place on open (see [Upgrading from v1](#upgrading-from-v1)).

## Identifiers

- Record `id` values are UUID strings: lowercase, 8-4-4-4-12 with hyphens.
- Shard assignment uses the **first byte** of the UUID (the first two hex characters of the hyphenated form).
- `shard_fanout` is 256, so shard stems are `00` … `ff` (lowercase hex) and v2 file names are `00.<writer>.jsonl` … `ff.<writer>.jsonl`.
- Example: `550e8400-e29b-41d4-a716-446655440000` → shard `55`.

## Timestamps

All `updated_at` / `created_at` values are UTC ISO-8601 with a `Z` suffix, `YYYY-MM-DDTHH:MM:SS` plus optional fractional seconds up to microseconds.

```
2026-08-25T15:04:05Z
2026-08-25T15:04:05.123Z
2026-08-25T15:04:05.123456Z
```

Compare as instants, not as strings. Writers should emit zero-padded microseconds (`%Y-%m-%dT%H:%M:%S.%fZ`).

## JSONL shards

Each shard is UTF-8 JSONL: one JSON object per line, newline-terminated. No UTF-8 BOM. Readers ignore blank lines.

A shard may contain **multiple versions of the same id**. The live version is selected by the LWW rule below (after applying tombstones). Writers should append a new line rather than rewriting the file on every mutation; compaction (dropping dominated lines) is optional and must be atomic (write temp file in the same directory, then replace).

Lines that are not valid JSON objects are skipped and should be reported by `graph-ted-db doctor`.

A hard halt can leave a shard **without a trailing newline** (a torn last line). An unterminated last line is never data: readers skip it and report it (`skipped ... (unterminated)`). Only the writer that owns a file (its id is in the name) repairs it: before every append, and on `GraphStore.open` / `doctor`, it **truncates** its own files from the last newline (or to empty). A file of another writer, or a v1 shared file, is **never modified**, because in a synced folder a missing newline usually means the sync client has not finished delivering it; `doctor` lists it as not modified. That is format recovery, not LWW.

`compact` rewrites shards, so it refuses to run on a shared store (any non-empty record file of another writer, any v1 shared file, or any conflict copy). Cleanup for shared stores is not available yet.

A transaction that writes several records appends its lines and then flush+fsyncs each file once. A local write-ahead record is fsynced before those shard writes and removed only after the shard fsync. If the process dies before that record is durable, the transaction is absent. If it dies after the record is durable but before the shard fsync finishes, the next open replays the record so the transaction is complete. An autocommit statement that writes one record fsyncs that line and does not write a separate commit record: the line is present, or a torn tail is truncated and the record is absent. A torn final line is not returned as a record.

## Node record

Path: `nodes/<shard>.<writer>.jsonl`

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

Path: `edges/<shard>.<writer>.jsonl` — shard is taken from the **edge id**, not from/to.

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

Path: `vectors/<property>/<shard>.<writer>.jsonl` — shard is the **subject id** (node or edge that owns the embedding). `<property>` is `[a-z][a-z0-9_]*` (examples: `name_embedding`, `fact_embedding`).

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

Path: `meta/deleted.<writer>.jsonl`

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

## Last-write-wins

Compared per **record id** (and vector property), not per file, and **not per property inside `props`**.

LWW only answers: when two complete versions of the **same** node, edge, or vector exist, which one is live? It is the concurrent-edit rule for a shared folder. It must **not** invent a third object, splice fields, or leave the graph half-applied.

1. Union every line for that id from the primary shard **and** any conflict-copy files for that shard.
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

The same rule applies to `meta/deleted.jsonl`, so forked tombstones still apply. In v2, every writer's file `NN.<writer>.jsonl` is part of the shard too (it is not a conflict copy), and so are conflict copies of it (`NN.<writer>.jsonl.conflict1`). Because v2 files have one writer, a sync client should never need to make conflict copies; the rule stays for v1 files and odd clients.

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

These must not be placed in the synced graph folder. v1 writes `catalog.jsonl`, `adj.jsonl`, and `meta.json` on open (always rebuilt from shards).

## What this format is not

- Not a single-file database.
- Not one file per node/edge (sync services limit file counts).
- Not CRDT merge of property maps. Whole-record LWW: one complete object wins; concurrent field-level edits on the same id can lose the non-winning write, they must not mix into a corrupt object.
- Not encrypted at rest by the library. Security is as strong as your storage and network (encrypted volumes and an airgap can be very strong; synced or shared folders are not). The folder ACL / disk encryption / network exposure you choose is the security boundary — see [Security](security.md).

## Upgrading from v1

A v1 folder has shared files (`nodes/NN.jsonl`, `meta/deleted.jsonl`) that any writer appended to. A v2 library reads them as part of each shard, never writes them again, and never modifies them (they are "v1 shared files" in `doctor`). On open it sets `format_version` to `2` and `layout` to `"per-writer"` in `graph.json`. A v1 library refuses to open the upgraded folder, which is intended: it would write to shared files again.
