# graphted

Thin meta distribution for the **graph-ted** kit. Installing it pulls in **`graphted-db`**, the local file graph store (SQLite for graphs). This project ships no importable modules.

| | |
|---|---|
| Distribution | `graphted` |
| Version | `0.1.0` |
| Depends on | `graphted-db>=0.1.0` |
| Import package | none |

Not published on PyPI or TestPyPI. The database project (distribution `graphted-db`, import `graph_ted_db`) is the repository root. Private install commands are in the root `README.md`.

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
