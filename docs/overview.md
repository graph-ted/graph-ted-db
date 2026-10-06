# graphted-db

**SQLite for graphs.** A local, file-based graph store you drop into a Python project or prototype: a folder in, a programmatic API out, network optional.

**Audience:** developers and agents prototyping or shipping simple and trusted-workgroup graph apps without standing up Neo4j. It is a lightweight substitute for Neo4j or FalkorDB in that niche, and it is not a multi-tenant server.

**This repository** is the database product. Distribution name `graphted-db`, import `graph_ted_db`. Install, open, put, and get are in the [README](../README.md). Public PyPI is not the install path.

**Stack:** graph-ted-db is the foundation of graph-ted Standalone. Hosted graph-ted may use other stores and must not run this one. The database stands on its own; the app is one client. Kit UI, agents, and ontology tooling live in the app repo, not here.

The library stores ordinary files and does not encrypt them. Security is the storage you choose plus whether you expose the network. See [Security](../README.md#security) in the README.

## How-to in this tree

- [README](../README.md) — install, Python open/put/get, CLI, optional HTTP, private wheels
- [On-disk format](format.md) — sharded JSONL. The folder is not a SQLite database file
- [Cypher subset](cypher.md) — power queries on an open store
- [HTTP](http.md) — optional localhost serve
