# Sharing a graph

**Sharing a graph through a sync service.** A graph folder can live in a folder that OneDrive, Dropbox or rclone syncs between devices. Multiple devices can add and edit at the same time; concurrent edits to the same record resolve to the latest version. Tested end to end with OneDrive via rclone; Dropbox and other sync services are expected to work. graph-ted-db never corrupts or splices records: when two devices change the same record, one complete version wins (last write wins), and conflict copies made by the sync tool are read and merged automatically. One exception in 0.1.2 and 0.1.3: a conflict copy of a record bundle (a file named like `w0123456789abcdef.000003.jsonl`) is read only when the sync tool adds its suffix after `.jsonl`, as rclone bisync does (`.jsonl.conflict1`, `.jsonl..path1`). Copies named with the suffix before `.jsonl` are not reliably read, so their records can stay hidden: OneDrive for Windows (`-DESKTOP-ABC123.jsonl`), abraunegg onedrive (`-hostname-safeBackup-0001.jsonl`), Dropbox (` (conflicted copy).jsonl`) and rclone bisync with `--suffix-keep-extension` (`.conflict1.jsonl`). With those versions, rename such a copy to the bundle's name plus `.conflict1` (for example `w0123456789abcdef.000003.jsonl.conflict1`). Releases after 0.1.3 read all of these names again, as 0.1.0 and 0.1.1 did. A multi-record transaction is atomic on the device that wrote it. Other devices see it only as whole sealed bundles, after the publish lag below and after their sync client copies those files.

## Limits

- **Clocks.** Device clocks decide which of two truly simultaneous edits wins. The result is deterministic, but a badly skewed clock can win.
- **Publish lag.** New records sit in this device's local app data until they are sealed (by default: every 1 MiB of a directory, every 2 seconds while writes continue, or 0.5 seconds after the last write; immediately on close). Another device cannot see them before that, and then still waits for sync. See [Sealed bundles](format.md#sealed-bundles).
- **History.** Each seal adds one file for the directory, not one file per shard. `compact()` (and automatic compaction after 4 bundles in that directory) republishes this writer's winners and deletes its older bundles. Another writer's files are left alone. A busy writer stays at a handful of files per directory.
- **Deleted record files.** If a record file in the graph folder is deleted on any device or on the remote (by hand, by a cleanup or dedupe tool, or by the sync service), the sync tool deletes it on every device. Since the release after 0.1.4, the device that wrote the file puts it back: each device keeps a local copy of every file it published (in its data directory, never in the graph folder), and on its next open, and within a few seconds while the graph is open, it republishes a missing file under the same name with the same bytes. The sync tool then copies it to every device and nothing is lost. `graph-ted-db info --check` and `doctor` report `restored N own files deleted by sync`. A device never reuses the name of a file it published. If `graph.json` is gone, or all of a device's files are gone at once, the device assumes the folder was deleted or moved on purpose and restores nothing; it reports it, and `graph-ted-db doctor --restore <folder>` puts the files back. Limits: a device restores only its own files, so a file whose writer no longer uses the folder is not restored, and a file deleted before the upgrade cannot be restored. Do not delete, move or rename files inside the graph folder. Keep deleted files recoverable as well: with rclone, use the backup flags below; OneDrive and Dropbox keep deleted files in their recycle bin or deleted-files list for a limited time. To recover by hand, copy the deleted file back into the same directory of the graph folder and let the sync tool upload it.
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
- Add `--backup-dir1 <dir> --backup-dir2 <dir>`, with both directories outside the graph folder (the second one on the remote). When bisync deletes or replaces a file on one side, it moves the old file there instead, so a deleted record file can be copied back.

### Recovering a stopped sync

**`prior lock file found`.** An earlier bisync run was killed before it finished. Make sure no other bisync is running for this folder, then either wait for `--max-lock` to expire or delete the lock file named in the message, and run bisync again.

**`Safety abort: too many deletes`.** bisync stops when more than half of the files look deleted since its last run. With two writers this can happen after many files conflicted: bisync renamed them to conflict copies and removed the originals, so the records are still there. If you did not delete files yourself, run bisync once with `--force`; later runs go back to normal.
