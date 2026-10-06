# graph-ted-db on-disk format

**Format version:** 1

This is the on-disk format of **graph-ted-db**, the database component of the **graph-ted** kit. A sync client (OneDrive, rclone, abraunegg) may copy, delay, or fork these files. Readers must treat the folder as eventually consistent and merge by record, not by whole file.

Derived indexes (catalog, inverted fulltext, unpacked vectors, adjacency cache) are **not** part of this format. They live outside the graph folder (or in a no-sync sidecar) and must be reconstructable from what is specified here.

There is no database engine in the graph folder: no SQLite, no WAL, no lock files that sync.

## Directory layout

One folder is one graph.

```
<graph>/
  graph.json                 # required
  nodes/
    00.jsonl                 # created on first write to that shard
    ff.jsonl
  edges/
    00.jsonl
  vectors/
    <property>/
      00.jsonl
  meta/
    labels.json              # optional hint; rebuildable
    deleted.jsonl            # tombstones; synced
  logs/                      # optional append-only mutation log; not required to open
```

Do **not** pre-create all 256 shard files. Empty files still count against OneDrive's item budget. Create a shard file when the first record for that shard is written.

## `graph.json`

UTF-8 JSON object.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `format` | string | yes | Always `"graph-ted-db"` |
| `format_version` | int | yes | `1` |
| `id` | string | yes | Graph UUID (lowercase, hyphenated) |
| `name` | string | yes | Human name; not unique |
| `created_at` | string | yes | UTC timestamp |
| `shard_fanout` | int | yes | `256` in v1 |

Unknown fields must be ignored by readers and preserved by writers that rewrite the file.

Example:

```json
{
  "format": "graph-ted-db",
  "format_version": 1,
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "name": "team-memory",
  "created_at": "2026-08-25T15:04:05.000000Z",
  "shard_fanout": 256
}
```

Opening a folder whose `format` is not `graph-ted-db`, or whose `format_version` is greater than the process understands, is an error. A lower version may be upgraded in place by a later format revision; v1 has no predecessor.

## Identifiers

- Record `id` values are UUID strings: lowercase, 8-4-4-4-12 with hyphens.
- Shard assignment uses the **first byte** of the UUID (the first two hex characters of the hyphenated form).
- v1 `shard_fanout` is 256, so shard filenames are `00.jsonl` … `ff.jsonl` (lowercase hex).
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

A hard halt can leave a shard **without a trailing newline** (a torn last line). Readers still skip that fragment, but a later append would concatenate a new record onto it and both lines would fail to parse. Before every append, and on `GraphStore.open` / `doctor`, writers **truncate** from the last newline (or to empty). The incomplete tail is discarded; every complete line is kept. That is format recovery, not LWW.

## Node record

Path: `nodes/<shard>.jsonl`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "v": 1,
  "updated_at": "2026-08-25T15:04:05.000000Z",
  "updated_by": "alice",
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
| `labels` | array of strings | yes (may be empty) |
| `props` | object | yes |

`props` must not contain embedding arrays. Embeddings live under `vectors/`. Property values are JSON: string, number, boolean, null, or arrays of those. Nested objects are allowed but not queried in v1 Cypher.

## Edge record

Path: `edges/<shard>.jsonl` — shard is taken from the **edge id**, not from/to.

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

Path: `vectors/<property>/<shard>.jsonl` — shard is the **subject id** (node or edge that owns the embedding). `<property>` is `[a-z][a-z0-9_]*` (examples: `name_embedding`, `fact_embedding`).

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

Path: `meta/deleted.jsonl`

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

Deterministic tie-break, in order:

1. Greater `updated_at` wins.
2. If equal, a tombstone beats a live record.
3. If still equal, greater `v` wins.
4. If still equal, lexicographically greater JSON line wins (last resort, so a replica can pick one).

### What LWW may lose

If Alice changes `props.name` and Bob changes `props.summary` on the same node without seeing each other, one complete snapshot wins and the other edit is dropped. Attribute-level merge (field clocks / CRDT of `props`) is **not** v1; whole-record LWW is the resolution a JSONL folder can support without corrupting objects.

### What LWW must not do

- Must not concatenate or splice two JSON objects into one malformed or mixed record. Torn-line repair (above) exists so a crash cannot fuse two writes.
- Must not produce a live node whose `props`/`labels` were taken half from Alice and half from Bob.
- Must not leave a **live edge whose endpoints are not live nodes**. `delete_node` tombstones incident edges (DETACH). Reads skip dangling edges. `doctor` tombstones any that remain (for example after a conflict-copy union). Compact omits dangling winners from the canonical shard.

Writer clocks can skew. Graphiti episodic history is the user-visible change log; this rule only decides the materialised record.

## OneDrive conflict copies

Sync clients fork a file when both sides edited it. Names vary; v1 treats **any extra `*.jsonl` in a shard directory** as a conflict sibling of the canonical `NN.jsonl` if:

- it is not exactly `NN.jsonl`, and
- the stem starts with the shard hex (`00`, `ff`, …) or contains the canonical filename stem.

Examples that must be ingested and union-merged:

```
nodes/00.jsonl
nodes/00-DESKTOP-NAME-conflict-2026-08-25.jsonl
nodes/00 (conflicted copy).jsonl
nodes/00-conflict-copy.jsonl
```

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
| `LOCK` | single-writer lock for this machine |

These must not be placed in the synced graph folder. v1 writes `catalog.jsonl`, `adj.jsonl`, and `meta.json` on open (always rebuilt from shards).

## What this format is not

- Not a single-file database.
- Not one file per node/edge (OneDrive item budget).
- Not CRDT merge of property maps. Whole-record LWW: one complete object wins; concurrent field-level edits on the same id can lose the non-winning write, they must not mix into a corrupt object.
- Not encrypted at rest by the library. Security is as strong as your storage and network (encrypted volumes and an airgap can be very strong; synced or shared folders are not). The folder ACL / disk encryption / network exposure you choose is the security boundary — see README Security.
