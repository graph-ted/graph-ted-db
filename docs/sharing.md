# Sharing a graph

**Sharing a graph through a sync service.** A graph folder can live in a folder that OneDrive, Dropbox or rclone syncs between devices. graph-ted-db never corrupts or splices records: when two devices change the same record, one complete version wins (last write wins), and conflict copies made by the sync tool are read and merged automatically. **Use one writer at a time:** finish writing and let the sync complete before writing from another device. Writing on two devices at once is not supported; a sync that runs while you write can drop recent writes. A multi-record transaction is atomic on the device that wrote it; other devices may briefly see part of it while files sync.

## If you use rclone

Use `rclone bisync` with these flags on every device:

```sh
rclone bisync ./my-graph remote:my-graph --resilient --recover --max-lock 2m
```

- The first run on each device also needs `--resync`.
- `--resilient --recover` let the next run pick up after an interrupted or failed run instead of asking for `--resync`.
- `--max-lock 2m` lets a lock file left behind by a killed run expire after two minutes.
- Keep the default `--conflict-resolve none`. bisync then keeps both versions of a file that changed on both sides (for example `00.jsonl.conflict1` and `00.jsonl.conflict2`), and graph-ted-db reads and merges them.
- Never use `--conflict-loser delete`. It deletes the losing copy, and with it every record that only that copy had.

### Recovering a stopped sync

**`prior lock file found`.** An earlier bisync run was killed before it finished. Make sure no other bisync is running for this folder, then either wait for `--max-lock` to expire or delete the lock file named in the message, and run bisync again.

**`Safety abort: too many deletes`.** bisync stops when more than half of the files look deleted since its last run. With two writers this can happen after many files conflicted: bisync renamed them to conflict copies and removed the originals, so the records are still there. If you did not delete files yourself, run bisync once with `--force`; later runs go back to normal.
