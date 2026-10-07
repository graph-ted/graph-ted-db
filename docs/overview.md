# Overview

graph-ted-db is a **local property-graph store** for Python applications. One directory on disk is one graph. Applications open that directory in-process; they do not need a database server for the default path.

## Package names

| | |
|--|--|
| Distribution | `graph-ted-db` |
| Import | `graph_ted_db` |
| CLI | `graph-ted-db` (alias `graphted-db`) |

## Capabilities

| Capability | Notes |
|------------|--------|
| Nodes / edges / vectors | Create, read, update, delete, iterate |
| Cypher subset | `GraphStore.execute` — see [Cypher](cypher.md) |
| Localhost HTTP | Optional serve — see [HTTP](http.md) |
| Sync-friendly files | Sharded JSONL, per-record last-write-wins, conflict-copy merge |
| NetworkX-style helpers | `add_node`, `add_edge`, `neighbors`, etc. (no algorithm suite) |

## Design in brief

- **One folder = one graph** = one share / ACL boundary.
- Canonical data is sharded JSONL. Derived indexes are local and rebuildable.
- Concurrent editors: last-write-wins per record; union-merge of conflict copies on read.
- Crash safety: one flush and fsync per multi-record transaction. A one-record autocommit fsyncs that line only. A crash loses at most the in-flight transaction. Torn lines are repaired on open; `graph-ted-db doctor` repairs dangling edges.
- Process locks live outside the synced folder (`GRAPH_TED_DB_DATA` / platform app data).

Full layout: [On-disk format](format.md).

## What this repository is not

This repository is the database library only. Application UI, agents, and higher-level toolkit pieces live elsewhere in the graph-ted family — see [Stack](stack.md).
