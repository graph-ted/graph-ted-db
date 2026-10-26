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

Results and rclone logs can contain the remote spec. The harness scrubs OneDrive and Graph upload URLs, and query parameters such as `access_token`, on each line before that line is written to a log or the results JSON. A run killed mid-sync does not leave the raw line on disk. Keep the results directory out of the repository (`sync-test-results/` is ignored) and run `python scripts/pii_scan.py` before committing anything derived from them.

Each bisync invocation writes its own log, `results/<run-id>-<n>-<phase>.log` (`sync`, `resync`, `force`, `overwrite`, `background`, `auto-resync`). A recovery run does not overwrite the log of the run that failed. Error lines, including `size changed from … to …`, are kept in full.

If bisync exits 7 and its workdir contains a `.lst-err` file, the harness runs one `--resync` so later scenarios are not stuck, and records both the lockout and the recovery as failures. A recovery that exits 0 does not turn the lockout into a success.

Writers seal one bundle per directory before a normal exit (`store.close()`). The default publish rule can also seal during a run (1 MiB for the directory, 2 seconds, or 0.5 seconds idle). A check that copies only the graph folder sees sealed bundles, not the local active log. Scenario s6a syncs whatever is already sealed, then reopens the killed writer (which publishes the rest) and syncs again.

## Run

On one machine, with a local directory as the remote:

```
python scripts/sync_test/sync_harness.py list
GTDB_SYNC_REMOTE=/tmp/gtdb-remote GTDB_SYNC_FOLDER=graph-ted-sync-test \
  python scripts/sync_test/sync_harness.py run s2 s4
```

On two machines that share a real rclone remote, run the same command on each machine with the same `GTDB_SYNC_REMOTE` and `GTDB_SYNC_FOLDER` and a distinct `GTDB_SYNC_WORK`. Do not point `GTDB_SYNC_REMOTE` at the root of the account. The remote folder should be empty of anything except this test. One machine can run the writers; the other only needs to sync if you are reproducing a desktop sync client beside rclone. The failure this layout is meant to remove is bisync reporting `can't copy - source file is being updated (size changed from A to B)` on a record file that is still growing, and then refusing every later sync until `--resync`. A publish adds one bundle per directory, so a long run does not upload one tiny file per shard.

```
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
