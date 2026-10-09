"""Command-line entry: `graph-ted-db` (alias `graphted-db`)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from graph_ted_db import __version__
from graph_ted_db.engine import CypherError
from graph_ted_db.store import GraphFormatError, GraphStore, init_graph, load_graph_meta

UPDATED_BY_ENV = "GRAPH_TED_DB_UPDATED_BY"


def _who(explicit: str | None) -> str:
    """Author string for written records: --by, else $GRAPH_TED_DB_UPDATED_BY, else empty.

    Never the OS login name: records travel with the folder when it is synced.
    """
    if explicit:
        return explicit
    return os.environ.get(UPDATED_BY_ENV, "")


def _parse_prop(raw: str) -> tuple[str, Any]:
    key, sep, value = raw.partition("=")
    if not key or sep != "=":
        raise argparse.ArgumentTypeError(f"expected key=value, got {raw!r}")
    try:
        return key, json.loads(value)
    except json.JSONDecodeError:
        return key, value


def _store(path: Path) -> GraphStore:
    return GraphStore.open(path.expanduser().resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="graph-ted-db",
        description="Local property-graph storage. Open a folder and query it in-process; no database server for the default path.",
        epilog="The graphted-db command is the same entry point.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="create an empty graph folder")
    init_p.add_argument("path", type=Path)
    init_p.add_argument("--name", default="graph")
    init_p.add_argument("--exist-ok", action="store_true")

    info_p = sub.add_parser("info", help="print graph.json for a folder")
    info_p.add_argument(
        "--check",
        action="store_true",
        help="also open the store and print problem counts (skipped lines, files still syncing)",
    )
    info_p.add_argument("path", type=Path)

    put_n = sub.add_parser("put-node", help="create or overwrite a node (LWW)")
    put_n.add_argument("path", type=Path)
    put_n.add_argument("--id", dest="record_id", default=None)
    put_n.add_argument("--label", action="append", default=[])
    put_n.add_argument("--prop", action="append", default=[], type=_parse_prop)
    put_n.add_argument(
        "--by",
        "--updated-by",
        dest="updated_by",
        default=None,
        help="author stored on the record (default: $GRAPH_TED_DB_UPDATED_BY, else empty)",
    )

    get_n = sub.add_parser("get-node", help="print one node as JSON")
    get_n.add_argument("path", type=Path)
    get_n.add_argument("record_id")

    ls_n = sub.add_parser("ls-nodes", help="print live nodes as JSON lines")
    ls_n.add_argument("path", type=Path)

    del_n = sub.add_parser("delete-node", help="tombstone a node")
    del_n.add_argument("path", type=Path)
    del_n.add_argument("record_id")
    del_n.add_argument(
        "--by",
        "--updated-by",
        dest="updated_by",
        default=None,
        help="author stored on the record (default: $GRAPH_TED_DB_UPDATED_BY, else empty)",
    )

    put_e = sub.add_parser("put-edge", help="create or overwrite an edge (LWW)")
    put_e.add_argument("path", type=Path)
    put_e.add_argument("--type", required=True, dest="edge_type")
    put_e.add_argument("--from", required=True, dest="from_id")
    put_e.add_argument("--to", required=True, dest="to_id")
    put_e.add_argument("--id", dest="record_id", default=None)
    put_e.add_argument("--prop", action="append", default=[], type=_parse_prop)
    put_e.add_argument(
        "--by",
        "--updated-by",
        dest="updated_by",
        default=None,
        help="author stored on the record (default: $GRAPH_TED_DB_UPDATED_BY, else empty)",
    )

    get_e = sub.add_parser("get-edge", help="print one edge as JSON")
    get_e.add_argument("path", type=Path)
    get_e.add_argument("record_id")

    ls_e = sub.add_parser("ls-edges", help="print live edges as JSON lines")
    ls_e.add_argument("path", type=Path)

    del_e = sub.add_parser("delete-edge", help="tombstone an edge")
    del_e.add_argument("path", type=Path)
    del_e.add_argument("record_id")
    del_e.add_argument(
        "--by",
        "--updated-by",
        dest="updated_by",
        default=None,
        help="author stored on the record (default: $GRAPH_TED_DB_UPDATED_BY, else empty)",
    )

    compact_p = sub.add_parser(
        "compact",
        help="rewrite canonical shards to LWW winners (keeps conflict copies)",
    )
    compact_p.add_argument("path", type=Path)

    doctor_p = sub.add_parser(
        "doctor",
        help="repair torn JSONL, rebuild labels, report dangling edges (--fix tombstones them)",
        description=(
            "Repair torn JSONL lines, remove leftover tmp files, rebuild labels.json, and report "
            "dangling edges (an endpoint is not a live node). Dangling edges are only reported "
            "unless --fix is given."
        ),
    )
    doctor_p.add_argument("path", type=Path)
    doctor_p.add_argument(
        "--fix",
        action="store_true",
        help=(
            "tombstone dangling edges. Permanent, and it syncs to every device: in a synced "
            "folder, wait until sync has finished, because an edge can arrive before its nodes"
        ),
    )

    cypher_p = sub.add_parser(
        "cypher", help="run an openCypher query (supported subset; see docs/cypher.md)"
    )
    cypher_p.add_argument("path", type=Path)
    cypher_p.add_argument("query", nargs="?", default=None, help="query; omit to read stdin")
    cypher_p.add_argument(
        "--params",
        default="{}",
        help="JSON object of query parameters",
    )
    cypher_p.add_argument(
        "--by",
        "--updated-by",
        dest="updated_by",
        default=None,
        help='author stored on written records (default: $GRAPH_TED_DB_UPDATED_BY, else "cypher")',
    )

    serve_p = sub.add_parser(
        "serve",
        help="localhost HTTP daemon (POST /cypher, GET /health); see docs/http.md",
    )
    serve_p.add_argument("path", type=Path, help="graph folder to serve")
    serve_p.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=None, help="TCP port (default 8099)")
    serve_p.add_argument(
        "--token",
        default=None,
        help="HTTP token (or GRAPH_TED_DB_TOKEN); required when --host is not loopback",
    )
    serve_p.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="truncate query results after this many rows (default 500)",
    )

    args, extra = parser.parse_known_args(argv)
    # Python < 3.12 argparse binds the optional `query` positional before it
    # sees later options, so `cypher PATH --params P QUERY` leaves QUERY over.
    if getattr(args, "command", None) == "cypher" and args.query is None and len(extra) == 1:
        args.query, extra = extra[0], []
    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    try:
        return _dispatch(args)
    except (
        OSError,
        RuntimeError,
        GraphFormatError,
        FileExistsError,
        ValueError,
        CypherError,
        json.JSONDecodeError,
    ) as exc:
        print(f"graph-ted-db: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        meta = init_graph(args.path, name=args.name, exist_ok=args.exist_ok)
        print(f"initialized {args.path} id={meta.id} name={meta.name}")
        return 0
    if args.command == "info":
        meta = load_graph_meta(args.path)
        print(
            f"{meta.name} id={meta.id} format={meta.format} "
            f"v{meta.format_version} created_at={meta.created_at}"
        )
        if args.check:
            store = _store(args.path)
            problems = store.problems()
            print(
                f"writer={store.writer_id} writers={len(store.writers())} "
                + " ".join(f"{k}={v}" for k, v in problems.items())
            )
            return 1 if any(problems.values()) else 0
        return 0

    store = _store(args.path)

    if args.command == "put-node":
        rec = store.make_node(
            labels=args.label,
            props=dict(args.prop),
            record_id=args.record_id,
            updated_by=_who(args.updated_by),
        )
        print(json.dumps(rec.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "get-node":
        found_node = store.get_node(args.record_id)
        if found_node is None:
            print(f"graph-ted-db: node {args.record_id} not found", file=sys.stderr)
            return 1
        print(json.dumps(found_node.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "ls-nodes":
        for rec in store.iter_nodes():
            print(json.dumps(rec.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "delete-node":
        tomb = store.delete_node(args.record_id, updated_by=_who(args.updated_by))
        print(json.dumps(tomb.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "put-edge":
        edge = store.make_edge(
            type=args.edge_type,
            from_id=args.from_id,
            to_id=args.to_id,
            props=dict(args.prop),
            record_id=args.record_id,
            updated_by=_who(args.updated_by),
        )
        print(json.dumps(edge.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "get-edge":
        found_edge = store.get_edge(args.record_id)
        if found_edge is None:
            print(f"graph-ted-db: edge {args.record_id} not found", file=sys.stderr)
            return 1
        print(json.dumps(found_edge.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "ls-edges":
        for edge in store.iter_edges():
            print(json.dumps(edge.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "delete-edge":
        tomb = store.delete_edge(args.record_id, updated_by=_who(args.updated_by))
        print(json.dumps(tomb.to_dict(), ensure_ascii=False))
        return 0
    if args.command == "compact":
        store.compact()
        print(f"compacted {args.path}")
        return 0
    if args.command == "doctor":
        report = store.doctor(fix=args.fix)
        print(report.summary(), end="")
        return 0
    if args.command == "cypher":
        query = args.query if args.query is not None else sys.stdin.read()
        params = json.loads(args.params) if args.params else {}
        if not isinstance(params, dict):
            raise ValueError("--params must be a JSON object")
        rows = store.execute(query, params, updated_by=_who(args.updated_by) or "cypher")
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.command == "serve":
        from graph_ted_db.server import (
            DEFAULT_HOST,
            DEFAULT_PORT,
            LOOPBACK_HOSTS,
            MAX_QUERY_RECORDS,
            serve,
        )

        host = args.host or DEFAULT_HOST
        port = DEFAULT_PORT if args.port is None else args.port
        max_records = MAX_QUERY_RECORDS if args.max_records is None else args.max_records
        if args.token is not None:
            token = args.token.strip()
        else:
            token = os.environ.get("GRAPH_TED_DB_TOKEN", "").strip()
        if host not in LOOPBACK_HOSTS and not token:
            print(
                "graph-ted-db: refusing to bind off loopback without a token "
                "(pass --token or set GRAPH_TED_DB_TOKEN)",
                file=sys.stderr,
            )
            return 1
        serve(store, host=host, port=port, max_records=max_records, token=token)
        return 0
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
