"""export / import round trip (backup)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from graph_ted_db import GraphStore, init_graph
from graph_ted_db.cli import main as cli_main
from graph_ted_db.store import import_export
from graph_ted_db.store.records import VectorRecord


def _graph(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="src")
    g = GraphStore.open(root, data_dir=tmp_path / "d")
    a = g.make_node(labels=["Person"], props={"name": "a"})
    b = g.make_node(labels=["Person"], props={"name": "b"})
    c = g.make_node(labels=["Person"], props={"name": "c"})
    g.make_edge(type="KNOWS", from_id=a.id, to_id=b.id, props={"w": 1})
    g.make_edge(type="KNOWS", from_id=b.id, to_id=c.id)
    g.make_node(record_id=a.id, labels=["Person"], props={"name": "a2"})
    g.delete_node(c.id)
    g.put_vector(
        VectorRecord.from_floats(
            id=a.id,
            property="name_embedding",
            values=[0.5, 1.0],
            updated_at=datetime.now(timezone.utc),
        )
    )
    return g


def _state(g: GraphStore):
    nodes = sorted((n.id, n.labels, tuple(sorted(n.props.items()))) for n in g.iter_nodes())
    edges = sorted((e.id, e.type, e.from_id, e.to_id) for e in g.iter_edges())
    return nodes, edges


def test_export_import_round_trip(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    out = tmp_path / "backup.jsonl"
    assert g.export(out) == {"nodes": 2, "edges": 1, "vectors": 1}
    copy = import_export(out, tmp_path / "restored", data_dir=tmp_path / "d2")
    assert copy.meta.id != g.meta.id
    assert copy.meta.name == "src"
    assert _state(copy) == _state(g)
    a = next(n for n in g.iter_nodes() if n.props["name"] == "a2")
    assert copy.get_vector("name_embedding", a.id).floats() == (0.5, 1.0)
    assert copy.problems() == dict.fromkeys(copy.problems(), 0)


def test_export_refuses_inside_graph_and_import_refuses_existing(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    with pytest.raises(ValueError):
        g.export(g.root / "backup.jsonl")
    out = tmp_path / "backup.jsonl"
    g.export(out)
    with pytest.raises(FileExistsError):
        import_export(out, g.root)
    bogus = tmp_path / "bogus.jsonl"
    bogus.write_text('{"hello": 1}\n')
    with pytest.raises(ValueError):
        import_export(bogus, tmp_path / "x")


def test_cli_export_import(tmp_path: Path, capsys, monkeypatch) -> None:
    g = _graph(tmp_path)
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "d"))
    out = tmp_path / "b.jsonl"
    assert cli_main(["export", str(g.root), str(out)]) == 0
    assert "nodes=2 edges=1 vectors=1" in capsys.readouterr().out
    assert cli_main(["import", str(out), str(tmp_path / "r")]) == 0
    restored = GraphStore.open(tmp_path / "r", data_dir=tmp_path / "d")
    assert _state(restored) == _state(g)
