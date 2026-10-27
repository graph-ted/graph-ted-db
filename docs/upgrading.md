# Upgrading

## Upgrading to 0.1.3

- No format change: 0.1.2 and 0.1.3 can share a folder.
- Comparing vectors of different sizes (`vector.similarity.cosine`) is now
  an error that names both sizes, instead of a score of 0.0. It means the
  graph mixes embedding models; re-embed it with one model.

## Upgrading to 0.1.2

0.1.2 writes on-disk format version 4. Older releases can't read it.

- **Upgrade every machine that shares a graph folder at the same time.** That
  includes every device that syncs the folder and every app or
  `graph-ted-db serve` process that opens it.
- **After 0.1.2 writes to a folder, 0.1.1 and older can't open it.** The first
  write from 0.1.2 sets `format_version` to 4 in `graph.json`. Opening or
  reading a folder with 0.1.2 doesn't change it; only a write does. There is
  no manual migration step, and there is no way back to 0.1.1 for that folder
  other than a [backup](backup.md) taken before the upgrade.
- **Check which version a machine has:**

    ```sh
    graph-ted-db --version
    pip show graph-ted-db
    ```

### What 0.1.2 shows on an older folder

When 0.1.2 or newer opens a folder that is still in the older format, it
reads it normally and prints one information line (on stderr from the
command line, at INFO level from Python's `logging`):

```text
graph-ted-db: This graph has files in an older storage format. Each machine upgrades them when it saves with graph-ted-db 0.1.2 or newer. Every machine sharing this folder needs 0.1.2 or newer.
```

This is not an error, and you don't need to run `doctor`. `info --check`
and `doctor` list the older files on an `info:` line and don't count them
as problems.

### What an older release shows

0.1.1 and 0.1.0 refuse the folder before reading or writing anything. Every
command (`info`, `ls-nodes`, `cypher`, `put-node`, `doctor`, `compact`,
`export`, `serve`, and so on) exits with status 1 and prints:

```text
graph-ted-db: graph format_version 4 is newer than this library (2)
```

In Python, `GraphStore(...)` raises `GraphFormatError` with the same message.
The folder is left exactly as it was. If you see this message, upgrade that
machine to 0.1.2 or later:

```sh
pip install --upgrade graph-ted-db
```
