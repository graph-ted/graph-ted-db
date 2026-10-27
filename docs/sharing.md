# Sharing a graph

**Sharing a graph through a sync service.** A graph folder can live in a folder that OneDrive, Dropbox or rclone syncs between devices. Multiple devices can add and edit at the same time; concurrent edits to the same record resolve to the latest version. Tested end to end with OneDrive via rclone; Dropbox and other sync services are expected to work. graph-ted-db never corrupts or splices records: when two devices change the same record, one complete version wins (last write wins), and conflict copies made by the sync tool are read and merged automatically. A multi-record transaction is atomic on the device that wrote it. Other devices see it only as whole sealed bundles, after the publish lag below and after their sync client copies those files.

## Limits

- **Clocks.** Device clocks decide which of two truly simultaneous edits wins. The result is deterministic, but a badly skewed clock can win.
- **Publish lag.** New records sit in this device's local app data until they are sealed (by default: every 1 MiB of a directory, every 2 seconds while writes continue, or 0.5 seconds after the last write; immediately on close). Another device cannot see them before that, and then still waits for sync. See [Sealed bundles](format.md#sealed-bundles).
- **History.** Each seal adds one file for the directory, not one file per shard. `compact()` (and automatic compaction after 4 bundles in that directory) republishes this writer's winners and deletes its older bundles. Another writer's files are left alone. A busy writer stays at a handful of files per directory.
- **Deleted writer files.** A writer's file deleted on the remote loses that writer's unsynced records.
- **OneDrive Files On-Demand.** Keep the graph folder **Always keep on this device**.
- **Versions.** Run the same graph-ted-db release on every device. A folder written by a newer format can't be opened by an older release; see [Upgrading](upgrading.md).

## If you use rclone

Use `rclone bisync` with these flags on every device:

```sh
rclone bisync ./my-graph remote:my-graph --resilient --recover --max-lock 2m
```

- The first run on each device also needs `--resync`.
- `--resilient --recover` let the next run pick up after an interrupted or failed run instead of asking for `--resync`.
- `--max-lock 2m` lets a lock file left behind by a killed run expire after two minutes.
- Keep the default `--conflict-resolve none`. bisync then keeps both versions of a file that changed on both sides (for example `00.jsonl.conflict1` and `00.jsonl.conflict2`), and graph-ted-db reads and merges them. Record bundles are immutable, so a normal two-writer run should not fork them.
- Never use `--conflict-loser delete`. It deletes the losing copy, and with it every record that only that copy had.

### Recovering a stopped sync

**`prior lock file found`.** An earlier bisync run was killed before it finished. Make sure no other bisync is running for this folder, then either wait for `--max-lock` to expire or delete the lock file named in the message, and run bisync again.

**`Safety abort: too many deletes`.** bisync stops when more than half of the files look deleted since its last run. With two writers this can happen after many files conflicted: bisync renamed them to conflict copies and removed the originals, so the records are still there. If you did not delete files yourself, run bisync once with `--force`; later runs go back to normal.
