# graph-ted-db

Local property-graph storage for Python. Your data lives in a folder on disk; open it from your process, query it in-process, and optionally sync that folder like any other files. No database server to run for the default path.

**Package:** `graph-ted-db` · **Import:** `graph_ted_db` · **Repo:** [graph-ted/graph-ted-db](https://github.com/graph-ted/graph-ted-db) · **License:** MIT

Good fit for prototypes, local tools, and small trusted groups that want a property graph next to the app. Choose a server graph database when you need multi-tenant hosting, fine-grained remote auth, or a full Cypher/enterprise feature set.

This repository is the database library only. It is the local store used by [graph-ted](https://github.com/graph-ted/graph-ted) standalone. Hosted deployments may use other backends. Install, the Python API, and the CLI are in the [README](../README.md).

The library stores ordinary files and does not encrypt them. Security matches the machine and the share. See [Security](../README.md#security) in the README.

## How-to in this tree

- [README](../README.md) — install, Python quick start, CLI, optional HTTP
- [On-disk format](format.md) — sharded JSONL in a folder
- [Cypher subset](cypher.md) — graph-pattern queries on an open store
- [HTTP](http.md) — optional localhost serve
