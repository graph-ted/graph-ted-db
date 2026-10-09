"""Two-writer file-sync test harness for graph-ted-db.

Two writers (A and B) each keep their own local copy of one graph folder and
their own ``rclone bisync`` state against the same remote folder, so they act
like two devices sharing a store through a file-sync service.

Nothing here names a real remote. Configuration comes from environment
variables or from an untracked ``sync_test.local.env`` next to this file
(gitignored):

    GTDB_SYNC_REMOTE   rclone remote spec ("name:" or a local directory). Required.
    GTDB_SYNC_FOLDER   dedicated test folder under the remote (default graph-ted-sync-test)
    GTDB_SYNC_WORK     local scratch root for writer folders and bisync state
    GTDB_SYNC_RESULTS  where results JSON and logs go (default <work>/results)
    GTDB_SYNC_RCLONE   rclone binary (default "rclone")
    GTDB_SYNC_BISYNC_FLAGS  extra bisync flags, space separated
    GTDB_SYNC_HOLD_LOCK  "1": hold the writer's graph-ted-db lock while bisync runs
    GTDB_SYNC_PARALLEL   "1": the two writers' bisync runs may overlap (one thread each)
    GTDB_SYNC_DOCTOR_FIX "1": scenario s9 runs doctor with fix=True (default: report only)

Usage:

    python scripts/sync_test/sync_harness.py run [scenario ...]
    python scripts/sync_test/sync_harness.py list

Every remote path the harness touches starts with
``$GTDB_SYNC_REMOTE + $GTDB_SYNC_FOLDER``. It never lists the remote root.
Results may contain the remote spec in rclone log lines; keep the results
directory out of version control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
NS = uuid.UUID("6f1c3f0e-6a3e-4c55-9d7c-5b1d1e0c2a11")


# --------------------------------------------------------------------------
# configuration


def _load_local_env() -> None:
    path = HERE / "sync_test.local.env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    remote: str
    folder: str
    work: Path
    results: Path
    rclone: str
    extra_flags: list[str]
    run_id: str

    @classmethod
    def from_env(cls) -> Config:
        _load_local_env()
        remote = os.environ.get("GTDB_SYNC_REMOTE", "").strip()
        if not remote:
            raise SystemExit("GTDB_SYNC_REMOTE is not set (env or sync_test.local.env)")
        folder = os.environ.get("GTDB_SYNC_FOLDER", "graph-ted-sync-test").strip("/")
        work = Path(os.environ.get("GTDB_SYNC_WORK") or tempfile.mkdtemp(prefix="gtdb-sync-"))
        results = Path(os.environ.get("GTDB_SYNC_RESULTS") or (work / "results"))
        rclone = os.environ.get("GTDB_SYNC_RCLONE", "rclone")
        extra = shlex.split(os.environ.get("GTDB_SYNC_BISYNC_FLAGS", ""))
        run_id = time.strftime("run-%Y%m%d-%H%M%S")
        return cls(remote, folder, work, results, rclone, extra, run_id)

    def remote_path(self, *parts: str) -> str:
        tail = "/".join([self.folder, self.run_id, *parts])
        if self.remote.endswith(":"):
            return self.remote + tail
        if ":" in self.remote and not self.remote.startswith("/"):
            return self.remote.rstrip("/") + "/" + tail
        return str(Path(self.remote) / tail)


# --------------------------------------------------------------------------
# record helpers


def rid(scenario: str, writer: str, kind: str, i: int) -> str:
    return str(uuid.uuid5(NS, f"{scenario}:{writer}:{kind}:{i}"))


def node_hash(labels: list[str] | tuple[str, ...], props: dict[str, Any]) -> str:
    blob = json.dumps({"labels": sorted(labels), "props": props}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def edge_hash(etype: str, src: str, dst: str, props: dict[str, Any]) -> str:
    blob = json.dumps({"type": etype, "from": src, "to": dst, "props": props}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# writer subprocess: applies a plan of operations and journals each commit


def _txn(store, fn) -> None:
    with store._lock():
        store._begin_unlocked()
        try:
            fn()
            store._commit_unlocked()
        except BaseException:
            store._rollback_unlocked()
            raise


def cmd_write(args: argparse.Namespace) -> int:
    from graph_ted_db.store import GraphStore
    from graph_ted_db.store.format import format_timestamp
    from graph_ted_db.store.records import EdgeRecord, NodeRecord

    plan = [json.loads(line) for line in Path(args.plan).read_text().splitlines() if line.strip()]
    store = GraphStore.open(args.store, data_dir=Path(args.data))
    journal = open(args.journal, "a", encoding="utf-8")
    batch: list[dict[str, Any]] = []

    def flush_batch() -> None:
        if not batch:
            return
        entries: list[dict[str, Any]] = []

        def body() -> None:
            for op in batch:
                stamp = format_timestamp()
                if op["op"] == "node":
                    rec = NodeRecord(
                        id=op["id"],
                        updated_at=stamp,
                        labels=tuple(op["labels"]),
                        props=op["props"],
                        updated_by=args.writer,
                    )
                    store.put_node(rec)
                    entries.append(
                        {
                            "kind": "node",
                            "id": op["id"],
                            "at": stamp,
                            "hash": node_hash(op["labels"], op["props"]),
                        }
                    )
                elif op["op"] == "edge":
                    rec = EdgeRecord(
                        id=op["id"],
                        updated_at=stamp,
                        type=op["type"],
                        from_id=op["from"],
                        to_id=op["to"],
                        props=op["props"],
                        updated_by=args.writer,
                    )
                    store.put_edge(rec)
                    entries.append(
                        {
                            "kind": "edge",
                            "id": op["id"],
                            "at": stamp,
                            "hash": edge_hash(op["type"], op["from"], op["to"], op["props"]),
                        }
                    )
                elif op["op"] in ("del_node", "del_edge"):
                    kind = "node" if op["op"] == "del_node" else "edge"
                    if kind == "node":
                        store._delete_node_unlocked(op["id"], updated_by=args.writer, now=stamp)
                    else:
                        store._delete_edge_unlocked(op["id"], updated_by=args.writer, now=stamp)
                    entries.append({"kind": kind, "id": op["id"], "at": stamp, "hash": None})

        if len(batch) == 1:
            body()
        else:
            _txn(store, body)
        txn = uuid.uuid4().hex[:8]
        for e in entries:
            e["writer"] = args.writer
            e["txn"] = txn
            journal.write(json.dumps(e) + "\n")
        journal.flush()
        batch.clear()

    for op in plan:
        if op["op"] == "sleep":
            flush_batch()
            time.sleep(op["s"])
            continue
        if op["op"] == "commit":
            flush_batch()
            continue
        batch.append(op)
        if len(batch) >= args.batch:
            flush_batch()
    flush_batch()
    journal.close()
    return 0


# --------------------------------------------------------------------------
# sync + checks


@dataclass
class Writer:
    cfg: Config
    scenario: str
    name: str
    remote: str
    base: Path = field(init=False)
    synced_once: bool = False
    sync_log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.base = self.cfg.work / self.scenario / self.name
        self.store.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.workdir.mkdir(parents=True, exist_ok=True)

    @property
    def store(self) -> Path:
        return self.base / "store"

    @property
    def data(self) -> Path:
        return self.base / "data"

    @property
    def workdir(self) -> Path:
        return self.base / "bisync"

    @property
    def journal(self) -> Path:
        return self.base / "journal.jsonl"

    def bisync_cmd(self, extra: list[str] | None = None) -> list[str]:
        cmd = [
            self.cfg.rclone,
            "bisync",
            str(self.store),
            self.remote,
            "--workdir",
            str(self.workdir),
            "--create-empty-src-dirs",
            "--resilient",
            "--recover",
            "--max-lock",
            "2m",
            "-v",
            *self.cfg.extra_flags,
            *(extra or []),
        ]
        if not self.synced_once:
            cmd.append("--resync")
        return cmd

    @property
    def logdir(self) -> Path:
        d = self.base / "sync-logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sync(self, extra: list[str] | None = None, *, timeout: float = 600) -> int:
        cmd = self.bisync_cmd(extra)
        t0 = time.time()
        log = self.logdir / f"{len(self.sync_log):04d}.log"
        with open(log, "w") as fh, self._store_lock():
            proc = subprocess.run(
                cmd, stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=timeout
            )
        out = log.read_text(errors="replace")
        self.sync_log.append(
            {
                "t": round(t0, 3),
                "rc": proc.returncode,
                "secs": round(time.time() - t0, 3),
                "resync": "--resync" in cmd,
                "log": log.name,
                "out": out[-4000:],
                "_full": out,
            }
        )
        if proc.returncode == 0:
            self.synced_once = True
        return proc.returncode

    def _store_lock(self):
        """With GTDB_SYNC_HOLD_LOCK=1, hold this writer's graph-ted-db LOCK during bisync.

        graph-ted-db writers take the same per-graph lock (in local app data,
        never in the synced folder), so no write can land while rclone lists
        and replaces files on this device.
        """
        import contextlib

        if (
            os.environ.get("GTDB_SYNC_HOLD_LOCK") != "1"
            or not (self.store / "graph.json").is_file()
        ):
            return contextlib.nullcontext()
        from graph_ted_db.store import load_graph_meta
        from graph_ted_db.store.lock import exclusive_lock, lock_path_for

        return exclusive_lock(lock_path_for(load_graph_meta(self.store).id, self.data))

    def start_sync(self, extra: list[str] | None = None) -> subprocess.Popen:
        log = self.logdir / f"{len(self.sync_log):04d}-bg.log"
        self.sync_log.append(
            {
                "t": round(time.time(), 3),
                "rc": None,
                "log": log.name,
                "background": True,
                "out": "",
                "_full": "",
            }
        )
        fh = open(log, "w")
        return subprocess.Popen(
            self.bisync_cmd(extra), stdout=fh, stderr=subprocess.STDOUT, text=True
        )

    def lock_files(self) -> list[Path]:
        return sorted(self.workdir.glob("*.lck"))

    def write(
        self,
        plan: list[dict[str, Any]],
        *,
        batch: int = 1,
        background: bool = False,
        env: dict[str, str] | None = None,
    ):
        plan_path = self.base / f"plan-{uuid.uuid4().hex[:6]}.jsonl"
        plan_path.write_text("".join(json.dumps(p) + "\n" for p in plan))
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "write",
            "--store",
            str(self.store),
            "--data",
            str(self.data),
            "--journal",
            str(self.journal),
            "--plan",
            str(plan_path),
            "--writer",
            self.name,
            "--batch",
            str(batch),
        ]
        full_env = dict(os.environ)
        full_env.update(env or {})
        if background:
            return subprocess.Popen(cmd, env=full_env)
        subprocess.run(cmd, check=True, env=full_env)
        return None


def node_op(
    scenario: str,
    writer: str,
    i: int,
    *,
    rev: int = 0,
    owner: str | None = None,
    label: str = "Entity",
) -> dict[str, Any]:
    owner = owner or writer
    props = {
        "scenario": scenario,
        "owner": owner,
        "i": i,
        "rev": rev,
        "by": writer,
        "payload": hashlib.sha256(f"{scenario}{owner}{i}{rev}{writer}".encode()).hexdigest(),
    }
    return {"op": "node", "id": rid(scenario, owner, "node", i), "labels": [label], "props": props}


def edge_op(
    scenario: str,
    writer: str,
    i: int,
    src: str,
    dst: str,
    *,
    rev: int = 0,
    owner: str | None = None,
) -> dict[str, Any]:
    owner = owner or writer
    props = {"scenario": scenario, "owner": owner, "i": i, "rev": rev, "by": writer}
    return {
        "op": "edge",
        "id": rid(scenario, owner, "edge", i),
        "type": "RELATES_TO",
        "from": src,
        "to": dst,
        "props": props,
    }


def read_journals(*writers: Writer) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for w in writers:
        if w.journal.is_file():
            for line in w.journal.read_text().splitlines():
                if line.strip():
                    out.append(json.loads(line))
    return out


def expected_state(entries: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Newest journaled version per (kind, id). hash None means deleted."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for e in entries:
        key = (e["kind"], e["id"])
        cur = best.get(key)
        cand = (e["at"], 1 if e["hash"] is None else 0)
        if cur is None or cand > (cur["at"], 1 if cur["hash"] is None else 0):
            best[key] = e
    return best


def raw_scan(store: Path) -> dict[str, Any]:
    """Every parseable record line under the folder, regardless of file name."""
    ids: dict[str, set[str]] = {"node": set(), "edge": set()}
    noncanonical: list[str] = []
    bad_lines = 0
    for sub, kind in (("nodes", "node"), ("edges", "edge"), ("meta", None)):
        d = store / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            canon = (len(p.name) == 8 and p.name.endswith(".jsonl")) or p.name in (
                "deleted.jsonl",
                "labels.json",
            )
            if not canon:
                noncanonical.append(f"{sub}/{p.name}")
            if kind is None:
                continue
            for raw in p.read_bytes().splitlines():
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                    ids[kind].add(obj["id"])
                except Exception:
                    bad_lines += 1
    return {
        "raw_node_ids": len(ids["node"]),
        "raw_edge_ids": len(ids["edge"]),
        "noncanonical_files": noncanonical,
        "raw_bad_lines": bad_lines,
        "_ids": ids,
    }


def check_replica(
    w: Writer, entries: list[dict[str, Any]], *, run_doctor: bool = True
) -> dict[str, Any]:
    """Open a copy of the replica the way a user would and compare to the journal."""
    from graph_ted_db.store import GraphStore

    res: dict[str, Any] = {"writer": w.name}
    scratch = Path(tempfile.mkdtemp(prefix=f"chk-{w.name}-", dir=w.base))
    copy = scratch / "store"
    shutil.copytree(w.store, copy)
    raw = raw_scan(copy)
    try:
        store = GraphStore.open(copy, data_dir=scratch / "data")
    except Exception as exc:  # noqa: BLE001
        res["opens"] = False
        res["open_error"] = repr(exc)
        shutil.rmtree(scratch, ignore_errors=True)
        return res
    res["opens"] = True
    nodes = {n.id: node_hash(n.labels, n.props) for n in store.iter_nodes()}
    edges = {e.id: edge_hash(e.type, e.from_id, e.to_id, e.props) for e in store.iter_edges()}
    versions: dict[tuple[str, str], set[str | None]] = {}
    for e in entries:
        versions.setdefault((e["kind"], e["id"]), set()).add(e["hash"])
    exp = expected_state(entries)
    live = {("node", k): v for k, v in nodes.items()}
    live.update({("edge", k): v for k, v in edges.items()})
    lost, stale, corrupt, unexpected, resurrected = [], [], [], [], []
    for key, e in exp.items():
        have = live.get(key)
        if e["hash"] is None:
            if have is not None:
                resurrected.append(key)
            continue
        if have is None:
            lost.append(key)
        elif have != e["hash"]:
            (stale if have in versions[key] else corrupt).append(key)
    for key in live:
        if key not in exp:
            unexpected.append(key)
    hidden = [k for k in lost if k[1] in raw["_ids"][k[0]]]
    res.update(
        {
            "expected_live": sum(1 for e in exp.values() if e["hash"] is not None),
            "live_nodes": len(nodes),
            "live_edges": len(edges),
            "lost": len(lost),
            "lost_but_on_disk": len(hidden),
            "stale_version": len(stale),
            "corrupt": len(corrupt),
            "unexpected": len(unexpected),
            "deleted_but_live": len(resurrected),
            "skipped_lines": len(store.skipped_lines),
            "raw_bad_lines": raw["raw_bad_lines"],
            "noncanonical_files": raw["noncanonical_files"][:50],
            "noncanonical_count": len(raw["noncanonical_files"]),
            "graph_conflict_copies": len(store._conflict_copy_paths()),
            "fingerprint": hashlib.sha256(
                json.dumps(sorted([f"{k[0]}:{k[1]}:{v}" for k, v in live.items()])).encode()
            ).hexdigest()[:16],
        }
    )
    if run_doctor:
        rep = store.doctor()
        res["doctor"] = {
            "torn_repaired": len(rep.torn_repaired),
            "tmp_removed": len(rep.tmp_removed),
            "dangling_found": len(rep.dangling_edges_found),
            "dangling_tombstoned": len(rep.dangling_edges_tombstoned),
            "skipped_lines": len(rep.skipped_lines),
            "conflict_copies": len(rep.conflict_copies),
        }
    shutil.rmtree(scratch, ignore_errors=True)
    return res


def converge(*writers: Writer, rounds: int = 3) -> list[int]:
    rcs = []
    for _ in range(rounds):
        for w in writers:
            rcs.append(w.sync())
    return rcs


def verdict(checks: list[dict[str, Any]]) -> dict[str, Any]:
    fps = {c.get("fingerprint") for c in checks}
    keys = (
        "lost",
        "lost_but_on_disk",
        "stale_version",
        "corrupt",
        "unexpected",
        "deleted_but_live",
        "skipped_lines",
    )
    agg = {k: max(c.get(k, 0) for c in checks) for k in keys}
    agg["all_open"] = all(c.get("opens") for c in checks)
    agg["replicas_identical"] = len(fps) == 1
    agg["clean"] = agg["all_open"] and agg["replicas_identical"] and not any(agg[k] for k in keys)
    return agg


# --------------------------------------------------------------------------
# scenarios


def setup_pair(cfg: Config, scenario: str) -> tuple[Writer, Writer]:
    from graph_ted_db.store import init_graph

    remote = cfg.remote_path(scenario, "store")
    subprocess.run([cfg.rclone, "mkdir", remote], check=True, capture_output=True)
    a = Writer(cfg, scenario, "A", remote)
    b = Writer(cfg, scenario, "B", remote)
    init_graph(a.store, name=f"sync-{scenario}", exist_ok=True)
    assert a.sync() == 0, a.sync_log[-1]["out"]
    assert b.sync() == 0, b.sync_log[-1]["out"]
    return a, b


def finish(
    name: str, a: Writer, b: Writer, notes: dict[str, Any], *, converge_rounds: int = 3
) -> dict[str, Any]:
    rcs = converge(a, b, rounds=converge_rounds)
    entries = read_journals(a, b)
    checks = [check_replica(a, entries), check_replica(b, entries)]
    stuck = [w.name for w in (a, b) if w.sync_log and w.sync_log[-1]["rc"] not in (0, None)]
    if stuck:
        # Record why bisync stopped, then try the manual recovery a user would
        # be told to run (--force past the delete safety check), and re-check.
        notes["stuck_writers"] = stuck
        notes["stuck_reason"] = sorted(
            {
                next(
                    (
                        l.split("ERROR :", 1)[-1].strip()[:120]
                        for l in w.sync_log[-1]["out"].splitlines()
                        if "ERROR" in l
                    ),
                    "?",
                )
                for w in (a, b)
                if w.name in stuck
            }
        )
        for _ in range(2):
            for w in (a, b):
                w.sync(["--force"])
        after = [
            check_replica(a, entries, run_doctor=False),
            check_replica(b, entries, run_doctor=False),
        ]
        notes["after_force_verdict"] = verdict(after)
    return {
        "scenario": name,
        "journaled_ops": len(entries),
        "converge_rcs": rcs,
        "checks": checks,
        "verdict": verdict(checks),
        "notes": notes,
        "sync_failures": [
            {k: v for k, v in s.items() if k != "_full"}
            for s in a.sync_log + b.sync_log
            if s["rc"] not in (0, None)
        ][:5],
        "conflict_lines": _conflict_lines(a, b),
    }


def _conflict_lines(*ws: Writer) -> list[str]:
    out = []
    for w in ws:
        for s in w.sync_log:
            for line in s.get("_full", s["out"]).splitlines():
                low = line.lower()
                if "conflict" in low or "renam" in low or "both path" in low:
                    out.append(f"{w.name}: " + line.split(":", 3)[-1].strip()[:200])
    return out[:40]


def sc1_sequential(cfg: Config, name: str = "s1_sequential") -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    a.write([node_op(name, "A", i) for i in range(50)])
    a.sync()
    b.sync()
    ids_a = [rid(name, "A", "node", i) for i in range(50)]
    b.write(
        [node_op(name, "B", i) for i in range(50)]
        + [edge_op(name, "B", i, ids_a[i], rid(name, "B", "node", i)) for i in range(50)]
        + [node_op(name, "B", i, rev=1, owner="A") for i in range(10)]
    )
    b.sync()
    a.sync()
    a.write([node_op(name, "A", i, rev=2, owner="B") for i in range(10)], batch=10)
    a.sync()
    b.sync()
    entries = read_journals(a, b)
    checks = [check_replica(a, entries), check_replica(b, entries)]
    return {
        "scenario": name,
        "journaled_ops": len(entries),
        "checks": checks,
        "verdict": verdict(checks),
        "notes": {"desc": "A writes, syncs; B syncs, writes, syncs; A syncs"},
        "conflict_lines": _conflict_lines(a, b),
    }


def _sync_loop(ws: list[tuple[Writer, float]], until: callable) -> None:
    """Sync each writer on its own interval until ``until()``.

    By default the writers' syncs run one after another. With
    GTDB_SYNC_PARALLEL=1 each writer syncs from its own thread, so the two
    devices' bisync runs can overlap against the remote.
    """
    if os.environ.get("GTDB_SYNC_PARALLEL") == "1":
        import threading

        def loop(w: Writer, iv: float) -> None:
            time.sleep(random.random() * iv)
            while not until():
                w.sync()
                time.sleep(iv)

        threads = [threading.Thread(target=loop, args=(w, iv)) for w, iv in ws]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return
    nxt = {w.name: time.time() + random.random() * iv for w, iv in ws}
    while not until():
        now = time.time()
        for w, iv in ws:
            if now >= nxt[w.name]:
                w.sync()
                nxt[w.name] = time.time() + iv
        time.sleep(0.05)


def sc2_concurrent(cfg: Config, name: str = "s2_concurrent", n: int = 300) -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    pa = a.write(
        [op for i in range(n) for op in (node_op(name, "A", i), {"op": "sleep", "s": 0.01})],
        background=True,
    )
    pb = b.write(
        [op for i in range(n) for op in (node_op(name, "B", i), {"op": "sleep", "s": 0.01})],
        background=True,
    )
    _sync_loop([(a, 1.0), (b, 1.3)], lambda: pa.poll() is not None and pb.poll() is not None)
    return finish(
        name,
        a,
        b,
        {
            "desc": f"A and B each write {n} distinct nodes while bisync runs every ~1s",
            "syncs": len(a.sync_log) + len(b.sync_log),
        },
    )


def sc3_offline(cfg: Config, name: str = "s3_offline", n: int = 200) -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    shared = [node_op(name, "S", i) for i in range(40)]
    a.write(shared, batch=40)
    a.sync()
    b.sync()
    # B goes offline. Both write; A keeps syncing.
    a_plan = [node_op(name, "A", i) for i in range(n)] + [
        node_op(name, "A", i, rev=1, owner="S") for i in range(20)
    ]
    b_plan = (
        [node_op(name, "B", i) for i in range(n)]
        + [node_op(name, "B", i, rev=2, owner="S") for i in range(20, 30)]
        + [{"op": "del_node", "id": rid(name, "S", "node", i)} for i in range(30, 40)]
    )
    for chunk in range(4):
        a.write(a_plan[chunk::4], batch=10)
        a.sync()
    b.write(b_plan, batch=10)
    # reconnect
    return finish(
        name,
        a,
        b,
        {
            "desc": f"B offline while A writes {n}+20 edits (syncing) and B writes {n}+10 edits+10 deletes; B reconnects"
        },
    )


def sc4_large(cfg: Config, name: str = "s4_large", n: int = 4000, ne: int = 2000) -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    plans = {}
    for w in ("A", "B"):
        ops = [node_op(name, w, i) for i in range(n)]
        ops += [
            edge_op(name, w, i, rid(name, w, "node", i), rid(name, w, "node", (i * 7 + 1) % n))
            for i in range(ne)
        ]
        plans[w] = ops
    pa = a.write(plans["A"], batch=100, background=True)
    pb = b.write(plans["B"], batch=100, background=True)
    _sync_loop([(a, 1.0), (b, 1.0)], lambda: pa.poll() is not None and pb.poll() is not None)
    return finish(
        name,
        a,
        b,
        {
            "desc": f"A and B each write {n} nodes + {ne} edges in 100-record transactions while syncing every ~1s",
            "syncs": len(a.sync_log) + len(b.sync_log),
        },
    )


def sc5_same_record(cfg: Config, name: str = "s5_same_record", rounds: int = 10) -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    base = [node_op(name, "S", i) for i in range(rounds)]
    base += [node_op(name, "S", 1000 + i) for i in range(rounds)]
    base += [
        edge_op(name, "S", i, rid(name, "S", "node", i), rid(name, "S", "node", 1000 + i))
        for i in range(rounds)
    ]
    a.write(base, batch=len(base))
    a.sync()
    b.sync()
    for r in range(rounds):
        src, dst = rid(name, "S", "node", r), rid(name, "S", "node", 1000 + r)
        a.write(
            [
                node_op(name, "A", r, rev=10, owner="S"),
                edge_op(name, "A", r, src, dst, rev=10, owner="S"),
            ],
            batch=2,
        )
        b.write(
            [
                node_op(name, "B", r, rev=20, owner="S"),
                edge_op(name, "B", r, src, dst, rev=20, owner="S"),
            ],
            batch=2,
        )
        a.sync()
        b.sync()
    return finish(
        name,
        a,
        b,
        {"desc": f"{rounds} rounds: A and B edit the same node and the same edge between syncs"},
    )


def sc6a_kill_writer(cfg: Config, name: str = "s6a_kill_writer", trials: int = 6) -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    trial_notes = []
    for t in range(trials):
        ops = [node_op(name, "A", t * 10000 + i) for i in range(3000)]
        p = a.write(ops, batch=200, background=True)
        time.sleep(0.4 + random.random() * 1.2)
        p.send_signal(signal.SIGKILL)
        p.wait()
        # A's sync client uploads whatever is on disk before A reopens.
        a.sync()
        b.sync()
        entries = read_journals(a, b)
        mid_b = check_replica(b, entries, run_doctor=False)
        wal = list((a.data).rglob("wal/*.jsonl"))
        wal_bytes = sum(p.stat().st_size for p in wal)
        # A reopens (WAL replay), syncs again.
        from graph_ted_db.store import GraphStore

        GraphStore.open(a.store, data_dir=a.data)
        a.sync()
        b.sync()
        trial_notes.append(
            {
                "trial": t,
                "b_before_a_reopen": {
                    k: mid_b.get(k)
                    for k in (
                        "opens",
                        "lost",
                        "unexpected",
                        "skipped_lines",
                        "raw_bad_lines",
                        "live_nodes",
                    )
                },
                "a_wal_bytes_pending": wal_bytes,
            }
        )
    res = finish(
        name,
        a,
        b,
        {
            "desc": f"{trials} trials: SIGKILL A mid-batch (200-record txns), A syncs immediately, B syncs and opens, then A reopens and syncs",
            "trials": trial_notes,
        },
    )
    # Journal lines are written after commit, so a killed writer's last
    # transaction can be present without a journal entry ("unexpected").
    # After WAL replay every trial must hold whole 200-record transactions.
    from graph_ted_db.store import GraphStore

    for w in (a, b):
        scratch = Path(tempfile.mkdtemp(dir=w.base))
        shutil.copytree(w.store, scratch / "s")
        live = {n.id for n in GraphStore.open(scratch / "s", data_dir=scratch / "d").iter_nodes()}
        per_trial = [
            sum(1 for i in range(3000) if rid(name, "A", "node", t * 10000 + i) in live)
            for t in range(trials)
        ]
        res["notes"][f"{w.name}_present_per_trial"] = per_trial
        res["notes"][f"{w.name}_whole_transactions"] = all(c % 200 == 0 for c in per_trial)
        shutil.rmtree(scratch, ignore_errors=True)
    return res


def sc6b_kill_rclone(cfg: Config, name: str = "s6b_kill_rclone", n: int = 6000) -> dict[str, Any]:
    a, b = setup_pair(cfg, name)
    a.write([node_op(name, "A", i) for i in range(n)], batch=500)
    notes: dict[str, Any] = {
        "desc": f"A writes {n} nodes; A's bisync is SIGKILLed mid-upload (bwlimit); B writes; "
        "then B's bisync is SIGKILLed mid-download; both recover"
    }
    p = a.start_sync(["--bwlimit", "300k"])
    time.sleep(2.0)
    p.send_signal(signal.SIGKILL)
    p.wait()
    notes["a_lock_left_behind"] = len(a.lock_files())
    remote_list = subprocess.run(
        [cfg.rclone, "lsf", "-R", a.remote], capture_output=True, text=True
    ).stdout
    notes["remote_files_after_kill"] = len(
        [l for l in remote_list.splitlines() if l.endswith(".jsonl")]
    )
    notes["remote_partial_files"] = [
        l for l in remote_list.splitlines() if "partial" in l or l.endswith(".tmp")
    ]
    b.write([node_op(name, "B", i) for i in range(200)], batch=50)
    notes["b_sync_rc"] = b.sync()
    notes["a_retry_rc_with_stale_lock"] = a.sync()
    notes["a_retry_error"] = next(
        (
            l.split("NOTICE:", 1)[-1].strip()[:160]
            for l in a.sync_log[-1]["out"].splitlines()
            if "Failed to bisync" in l
        ),
        None,
    )
    # Documented recovery: remove the stale lock (or wait for --max-lock), then rerun with --recover.
    for lck in a.lock_files():
        lck.unlink()
    notes["a_rc_after_lock_removed"] = a.sync()
    b.sync()
    entries = read_journals(a, b)
    notes["b_after_a_recovery"] = {
        k: v
        for k, v in check_replica(b, entries, run_doctor=False).items()
        if k in ("opens", "lost", "corrupt", "skipped_lines", "live_nodes")
    }
    # Now kill B mid-download of a fresh large change from A.
    a.write([node_op(name, "A", 100000 + i) for i in range(n)], batch=500)
    a.sync()
    p = b.start_sync(["--bwlimit", "300k"])
    time.sleep(1.5)
    p.send_signal(signal.SIGKILL)
    p.wait()
    notes["local_b_partial_files"] = [
        str(x.relative_to(b.store)) for x in b.store.rglob("*") if "partial" in x.name
    ]
    entries = read_journals(a, b)
    notes["b_open_after_kill"] = {
        k: v
        for k, v in check_replica(b, entries, run_doctor=False).items()
        if k in ("opens", "lost", "corrupt", "skipped_lines", "raw_bad_lines", "live_nodes")
    }
    for lck in b.lock_files():
        lck.unlink()
    return finish(name, a, b, notes)


def sc7_open_during_sync(
    cfg: Config, name: str = "s7_open_during_sync", n: int = 6000
) -> dict[str, Any]:
    from graph_ted_db.store import GraphStore

    a, b = setup_pair(cfg, name)
    a.write([node_op(name, "A", i) for i in range(n)], batch=500)
    a.sync()
    p = b.start_sync(["--bwlimit", "400k"])
    samples = []
    t0 = time.time()
    store = None
    while p.poll() is None and time.time() - t0 < 300:
        time.sleep(0.7)
        try:
            store = GraphStore.open(b.store, data_dir=b.data)
            cnt = sum(1 for _ in store.iter_nodes())
            samples.append(
                {
                    "t": round(time.time() - t0, 1),
                    "live_nodes": cnt,
                    "skipped": len(store.skipped_lines),
                }
            )
        except Exception as exc:  # noqa: BLE001
            samples.append({"t": round(time.time() - t0, 1), "error": repr(exc)})
    p.wait()
    after = sum(1 for _ in store.iter_nodes()) if store else None
    notes = {
        "desc": f"B opens and reads the store repeatedly while bisync downloads {n} nodes (bwlimit)",
        "samples": samples[:30],
        "open_store_after_sync_live_nodes": after,
        "sync_rc": p.returncode,
    }
    return finish(name, a, b, notes)


def sc8_native_conflict_names(cfg: Config, name: str = "s8_native_names") -> dict[str, Any]:
    """Conflict copies named the way sync clients name them (no rclone involved)."""
    from graph_ted_db.store import GraphStore, init_graph

    base = cfg.work / name
    results = {}
    for label, pattern in {
        "windows_client": "{s}-DESKTOP-ABC123.jsonl",
        "abraunegg_safebackup": "{s}-hostname-safeBackup-0001.jsonl",
        "dropbox_style": "{s} (conflicted copy).jsonl",
        "rclone_bisync_default": "{s}.jsonl.conflict1",
        "rclone_bisync_legacy": "{s}.jsonl..path1",
        "rclone_bisync_keep_ext": "{s}.conflict1.jsonl",
    }.items():
        root = base / label
        init_graph(root, name=label, exist_ok=True)
        s = GraphStore.open(root, data_dir=base / f"{label}-data")
        n = s.make_node(labels=["X"], props={"k": 1}, record_id=rid(name, label, "node", 0))
        shard = s.paths.node_shard(n.id)
        fork = shard.with_name(pattern.format(s=shard.stem))
        os.replace(shard, fork)
        s2 = GraphStore.open(root, data_dir=base / f"{label}-data2")
        results[label] = {
            "file": "nodes/" + pattern.format(s="NN"),
            "record_visible": s2.get_node(n.id) is not None,
        }
    return {
        "scenario": name,
        "notes": {"desc": "canonical shard renamed to each client's conflict-copy pattern"},
        "results": results,
    }


def sc9_doctor_partial(cfg: Config, name: str = "s9_doctor_partial", n: int = 50) -> dict[str, Any]:
    """B runs doctor after edges arrived but before their nodes did."""
    from graph_ted_db.store import GraphStore

    a, b = setup_pair(cfg, name)
    ops = [node_op(name, "A", i) for i in range(n)]
    ops += [
        edge_op(name, "A", i, rid(name, "A", "node", i), rid(name, "A", "node", (i + 1) % n))
        for i in range(n)
    ]
    a.write(ops, batch=len(ops))
    a.sync()
    # Partial arrival: only edges/ and meta/ reach B first (a sync interrupted, or a slow client).
    subprocess.run(
        [
            cfg.rclone,
            "copy",
            a.remote,
            str(b.store),
            "--include",
            "edges/**",
            "--include",
            "meta/**",
        ],
        check=True,
        capture_output=True,
    )
    fix = os.environ.get("GTDB_SYNC_DOCTOR_FIX") == "1"
    rep = GraphStore.open(b.store, data_dir=b.data).doctor(fix=fix)
    notes = {
        "desc": f"A writes {n} nodes + {n} edges in one transaction; B receives edges before nodes and runs "
        f"doctor{' --fix' if fix else ''}",
        "doctor_dangling_found": len(rep.dangling_edges_found),
        "doctor_dangling_tombstoned": len(rep.dangling_edges_tombstoned),
    }
    return finish(name, a, b, notes)


def sc10_delete_safety(
    cfg: Config, name: str = "s10_delete_safety", n: int = 3000
) -> dict[str, Any]:
    """Both writers touch most shards between syncs; bisync's delete safety check trips."""
    a, b = setup_pair(cfg, name)
    a.write([node_op(name, "A", i) for i in range(n)], batch=500)
    a.sync()
    b.sync()
    a.write([node_op(name, "A", 10000 + i) for i in range(n)], batch=500)
    b.write([node_op(name, "B", i) for i in range(n)], batch=500)
    notes: dict[str, Any] = {
        "desc": f"A and B each write {n} nodes (touching most shards) between syncs",
        "rcs": {"B": b.sync(), "A": a.sync()},
    }
    notes["retry_rcs"] = [(a.sync(), b.sync()) for _ in range(2)]
    entries = read_journals(a, b)
    notes["before_force_verdict"] = verdict(
        [check_replica(a, entries, run_doctor=False), check_replica(b, entries, run_doctor=False)]
    )
    return finish(name, a, b, notes, converge_rounds=1)


SCENARIOS = {
    "s1": sc1_sequential,
    "s2": sc2_concurrent,
    "s3": sc3_offline,
    "s4": sc4_large,
    "s5": sc5_same_record,
    "s6a": sc6a_kill_writer,
    "s6b": sc6b_kill_rclone,
    "s7": sc7_open_during_sync,
    "s8": sc8_native_conflict_names,
    "s9": sc9_doctor_partial,
    "s10": sc10_delete_safety,
}


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    if args.tag:
        cfg.run_id += "-" + args.tag
    cfg.work.mkdir(parents=True, exist_ok=True)
    cfg.results.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    names = args.scenarios or list(SCENARIOS)
    out_path = cfg.results / f"{cfg.run_id}.json"
    report: dict[str, Any] = {
        "run_id": cfg.run_id,
        "extra_flags": cfg.extra_flags,
        "rclone": subprocess.run(
            [cfg.rclone, "version"], capture_output=True, text=True
        ).stdout.splitlines()[:1],
        "results": [],
    }
    for key in names:
        t0 = time.time()
        try:
            res = SCENARIOS[key](cfg)
        except Exception as exc:  # noqa: BLE001
            import traceback

            res = {"scenario": key, "error": repr(exc), "trace": traceback.format_exc()[-3000:]}
        res["secs"] = round(time.time() - t0, 1)
        report["results"].append(res)
        out_path.write_text(json.dumps(report, indent=2, default=str))
        v = res.get("verdict") or res.get("results") or res.get("error")
        print(f"{key}: {json.dumps(v, default=str)}", flush=True)
    print(f"results: {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenarios", nargs="*", choices=[*SCENARIOS, []] and list(SCENARIOS))
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--tag", default="")
    sub.add_parser("list")
    w = sub.add_parser("write")
    for a in ("--store", "--data", "--journal", "--plan", "--writer"):
        w.add_argument(a, required=True)
    w.add_argument("--batch", type=int, default=1)
    args = p.parse_args(argv)
    if args.cmd == "write":
        return cmd_write(args)
    if args.cmd == "list":
        for k, fn in SCENARIOS.items():
            print(k, fn.__name__)
        return 0
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
