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
| openCypher queries (subset) | `GraphStore.execute` — see [openCypher subset](cypher.md) |
| Localhost HTTP | Optional serve — see [HTTP](http.md) |
| Sync-friendly files | Sharded JSONL, per-record last-write-wins, conflict-copy merge |
| NetworkX-style helpers | `add_node`, `add_edge`, `neighbors`, etc. (no algorithm suite) |

## Design in brief

- **One folder = one graph** = one share / ACL boundary.
- Canonical data is sharded JSONL. Derived indexes are local and rebuildable.
- Concurrent editors: last-write-wins per record; union-merge of conflict copies on read.
- Crash safety (tested on Linux and macOS; Windows is not yet covered by the crash tests): one flush and fsync per multi-record transaction. A one-record autocommit fsyncs that line only. A crash loses at most the in-flight transaction. Torn lines are repaired on open. `graph-ted-db doctor` reports dangling edges; `doctor --fix` tombstones them.
- Process locks live outside the synced folder (`GRAPH_TED_DB_DATA` / platform app data).

Full layout: [On-disk format](format.md).

## What this repository is not

This repository is the database library only. Application UI, agents, and higher-level toolkit pieces live elsewhere in the graph-ted family — see [Stack](stack.md).

## Graph size

graph-ted-db loads the whole graph into memory when a store opens, so open time
and memory grow with the number of records. Measured on one Linux machine
(Python 3.13, small properties, one writer):

| Nodes | Edges | Open | Filter query (full label scan) | Memory |
|------:|------:|-----:|-------------------------------:|-------:|
| 10,000 | 5,000 | 0.9 s | 0.05 s | 50 MB |
| 100,000 | 50,000 | 8 s | 0.9 s | 310 MB |
| 250,000 | 125,000 | 22 s | 2.7 s | 660 MB |
| 500,000 | 250,000 | 44 s | 4.8 s | 1.3 GB |

**Comfortable size: up to about 100,000 records (nodes plus edges).** Beyond
about 250,000 records, opening takes tens of seconds and memory passes 600 MB.
Larger graphs work, but a server graph database is a better fit. Lookups by id
stay fast at every size. Pattern and filter queries scan the matching label.
