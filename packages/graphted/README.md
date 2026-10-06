# graphted

Thin meta distribution for the **graph-ted** kit. Installing it pulls in **`graphted-db`**, local property-graph storage for Python (no database server for the default path). This project ships no importable modules.

| | |
|---|---|
| Distribution | `graphted` |
| Version | `0.1.0` |
| Depends on | `graphted-db>=0.1.0` |
| Import package | none |

The database project (distribution `graphted-db`, import `graph_ted_db`) is the repository root. Public install for the store is `pip install graphted-db`; see the root `README.md`. This meta package is not on PyPI yet — build and install from a local wheel as below.

Build this wheel from the repository root (writes `dist/` next to the database wheel):

```bash
python -m pip install build
python -m build --outdir dist packages/graphted
```

Or from this directory (writes `packages/graphted/dist/`):

```bash
python -m build
```

Install from a local wheelhouse that already contains both wheels (does not contact PyPI):

```bash
python -m pip install --no-index --find-links /path/to/dist graphted
```
