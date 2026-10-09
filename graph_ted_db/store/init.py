"""Create and open a graph folder."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from graph_ted_db.store.format import (
    LAYOUT_PER_WRITER,
    format_timestamp,
    is_writer_id,
    new_writer_id,
)
from graph_ted_db.store.jsonl import replace_json_file
from graph_ted_db.store.paths import GraphPaths
from graph_ted_db.store.records import EMPTY_LABELS, GraphMeta


class GraphFormatError(ValueError):
    """The folder is not a readable graph-ted-db graph."""


def init_graph(
    path: str | Path,
    *,
    name: str = "graph",
    exist_ok: bool = False,
) -> GraphMeta:
    """Create an empty graph folder. Refuses to clobber an existing graph.json."""
    root = Path(path)
    paths = GraphPaths(root)
    if paths.graph_json.exists() and not exist_ok:
        raise FileExistsError(f"already a graph: {paths.graph_json}")
    if paths.graph_json.exists() and exist_ok:
        return load_graph_meta(root)

    root.mkdir(parents=True, exist_ok=True)
    for directory in paths.required_directories():
        directory.mkdir(parents=True, exist_ok=True)

    meta = GraphMeta(
        id=str(uuid4()),
        name=name,
        created_at=format_timestamp(),
        extras={"layout": LAYOUT_PER_WRITER},
    )
    replace_json_file(paths.graph_json, meta.to_dict())
    replace_json_file(paths.labels_json, EMPTY_LABELS)
    paths.writers_dir.mkdir(parents=True, exist_ok=True)
    return meta


def load_graph_meta(path: str | Path) -> GraphMeta:
    root = Path(path)
    graph_json = GraphPaths(root).graph_json
    if not graph_json.is_file():
        raise GraphFormatError(f"missing {graph_json}")
    try:
        data = json.loads(graph_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GraphFormatError(f"invalid JSON in {graph_json}") from exc
    if not isinstance(data, dict):
        raise GraphFormatError("graph.json must be an object")
    try:
        return GraphMeta.from_dict(data)
    except ValueError as exc:
        raise GraphFormatError(str(exc)) from exc


def load_or_create_writer_id(path: Path) -> str:
    """Read this device's writer id, or create a new random one.

    The id is random ("w" + 16 hex digits) and never derived from a user,
    OS, or host name. If the file is lost, a new id is created; an old id is
    never reused, so the old files simply become another writer's.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        writer = data.get("writer") if isinstance(data, dict) else None
        if isinstance(writer, str) and is_writer_id(writer):
            return writer
    except (OSError, json.JSONDecodeError):
        pass
    writer = new_writer_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_json_file(path, {"writer": writer})
    return writer
