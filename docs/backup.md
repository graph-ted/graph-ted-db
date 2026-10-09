# Backup and restore

A graph is a folder of ordinary files, so any file backup works. Two things
make a backup trustworthy: nothing should be writing while you copy, and the
copy should be complete.

## A sync service is not a backup

A sync client copies deletes and mistakes to every device within seconds. If
a file is deleted or replaced on one device or in the cloud, the sync client
will usually remove or replace it everywhere. Keep at least one copy that the
sync client does not manage.

## Option 1: copy the folder

1. Stop processes that write to the graph on this device (`graph-ted-db serve`,
   your app).
2. Wait until your sync client shows the folder as up to date.
3. Copy the whole graph folder somewhere outside the synced folder, for
   example a dated zip archive.

The copy includes every writer's files and the full history. Restoring is
copying the folder back. Restoring an old copy into a folder that still syncs
brings back old files, so restore into a new location, or pause sync first.
You don't need to back up local app data: indexes and the write-ahead log
are rebuilt from the folder.

## Option 2: export

```bash
graph-ted-db export path/to/graph backup-2026-10-09.jsonl
graph-ted-db import backup-2026-10-09.jsonl path/to/restored-graph
```

`export` writes the current state (the live version of every node, edge and
embedding) to one JSONL file. It leaves out history, tombstones and conflict
copies, and it takes the local lock, so it is consistent even while other
processes on the device write. The export must be written outside the graph
folder.

`import` creates a **new** graph folder, with a new graph id, from an export
file. Records keep their ids and timestamps. It refuses to write into an
existing graph.

In Python:

```python
from graph_ted_db import GraphStore
from graph_ted_db.store import import_export

store = GraphStore.open("path/to/graph")
store.export("backup.jsonl")
restored = import_export("backup.jsonl", "path/to/restored-graph")
```

## Check a backup

```bash
graph-ted-db info --check path/to/restored-graph
graph-ted-db doctor path/to/restored-graph
```

`info --check` exits with status 1 if any lines were skipped or any files look
incomplete.
