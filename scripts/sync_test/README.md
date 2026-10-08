# Two-writer file-sync test

`sync_harness.py` checks what happens to a graph-ted-db store shared by two writers through a file-sync service. Each writer (A and B) has its own local copy of the graph folder, its own graph-ted-db app-data directory, and its own `rclone bisync` state, all against one remote folder, so they behave like two devices.

Not part of the default test run. It needs `rclone` and a remote you are allowed to write to. A local directory works as the remote for rclone-only behaviour.

## Configure

Set environment variables, or copy `sync_test.local.env.example` to `sync_test.local.env` (gitignored) and fill it in:

| Variable | Meaning |
|---|---|
| `GTDB_SYNC_REMOTE` | rclone remote spec (`name:`) or a local directory. Required. |
| `GTDB_SYNC_FOLDER` | Dedicated test folder under the remote. Default `graph-ted-sync-test`. Every remote path starts here. |
| `GTDB_SYNC_WORK` | Local scratch root (writer folders, bisync state). Default: a new temp dir. |
| `GTDB_SYNC_RESULTS` | Results JSON. Default `<work>/results`. |
| `GTDB_SYNC_RCLONE` | rclone binary. Default `rclone`. |
| `GTDB_SYNC_BISYNC_FLAGS` | Extra bisync flags, e.g. `--conflict-suffix conflict --suffix-keep-extension`. |
| `GTDB_SYNC_HOLD_LOCK` | `1` = hold the writer's graph-ted-db lock while bisync runs on that device. |
| `GTDB_SYNC_PARALLEL` | `1` = the two writers sync from separate threads, so their bisync runs can overlap. |

Results and rclone logs can contain the remote spec. Keep them out of the repository (`sync-test-results/` is ignored) and run `python scripts/pii_scan.py` before committing anything derived from them.

## Run

```
python scripts/sync_test/sync_harness.py list
python scripts/sync_test/sync_harness.py run            # all scenarios
python scripts/sync_test/sync_harness.py run s2 s5      # some
```

| Id | Scenario |
|---|---|
| s1 | Sequential handoff: A writes, syncs; B syncs, writes, syncs; A syncs |
| s2 | Concurrent writes to different nodes, both syncing on an interval |
| s3 | One writer offline while both write, then reconnects |
| s4 | Large batches (thousands of records, 100-record transactions) with syncs during the writes |
| s5 | Both writers edit the same node and the same edge between syncs |
| s6a | SIGKILL a writer mid-batch; its copy syncs before it reopens |
| s6b | SIGKILL rclone mid-upload and mid-download; recover |
| s7 | Open and read the store while a sync is downloading |
| s8 | Conflict-copy file names from several sync clients (no rclone) |
| s9 | `doctor` on a copy where edges arrived before their nodes (`GTDB_SYNC_DOCTOR_FIX=1` for `--fix`) |
| s10 | Both writers touch most shards between syncs; bisync's delete safety check stops one side until `--force` |

Record ids and contents are deterministic. Each writer journals every committed record; after the replicas converge the check opens a copy of each replica and reports records lost, lost but still on disk, stale (an older version won), corrupt (content no writer wrote), unexpected, skipped lines, `doctor` output, and whether both replicas are identical.

Delete the local work directory afterwards. The remote test folder is left in place.
