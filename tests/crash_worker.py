"""Subprocess writer for crash tests. Not collected as a test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from graph_ted_db.store import GraphStore

KEEP_SEQS = range(6)
DROP_SEQ = 6
DROP_EDGE_SEQ = 7
KEEP_EDGE_SEQ = 8


def record_id(txn: int, seq: int) -> str:
    return f"{seq:02x}{txn:06x}-0000-4000-8000-000000000000"


def _query() -> str:
    creates = [
        (f"CREATE (k{seq}:Entity {{uuid: $k{seq}, name: 'keep', txn: $txn, role: 'keep'}})")
        for seq in KEEP_SEQS
    ]
    creates.append("CREATE (d:Entity {uuid: $drop, name: 'drop', txn: $txn, role: 'drop'})")
    creates.append("CREATE (k0)-[:RELATES_TO {uuid: $keep_edge, txn: $txn, role: 'keep'}]->(k1)")
    creates.append("CREATE (k0)-[:RELATES_TO {uuid: $drop_edge, txn: $txn, role: 'drop'}]->(d)")
    creates.append("DELETE d")
    return "\n".join(creates)


_CYPHER = _query()


def apply_one(store: GraphStore, txn: int) -> None:
    """One autocommit statement, one record."""
    store.execute(
        "CREATE (:Entity {uuid: $uuid, name: 'one', txn: $txn})",
        {"uuid": record_id(txn, 0), "txn": txn},
    )


def apply_txn(store: GraphStore, txn: int) -> None:
    params: dict[str, object] = {
        "txn": txn,
        "drop": record_id(txn, DROP_SEQ),
        "drop_edge": record_id(txn, DROP_EDGE_SEQ),
        "keep_edge": record_id(txn, KEEP_EDGE_SEQ),
    }
    for seq in KEEP_SEQS:
        params[f"k{seq}"] = record_id(txn, seq)
    store.execute(_CYPHER, params)


def _write_intent(path: Path, txn: int) -> None:
    """Publish the in-flight transaction id atomically.

    Truncating ``intent`` in place lets a SIGKILL land between the truncate
    and the write, which leaves an empty file. A temp file, fsync, and
    ``os.replace`` publishes either the previous id or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = str(txn).encode("ascii")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dirfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)


def main(argv: list[str]) -> None:
    root = Path(argv[1])
    data = Path(argv[2])
    store = GraphStore.open(root, data_dir=data)
    intent = data / "intent"
    write = apply_one if len(argv) > 3 and argv[3] == "single" else apply_txn
    txn = 0
    while True:
        _write_intent(intent, txn)
        write(store, txn)
        sys.stdout.write(f"{txn}\n")
        sys.stdout.flush()
        txn += 1


if __name__ == "__main__":
    main(sys.argv)
