"""0.1.0 polish: count(*), unaliased RETURN columns, temp names, CLI errors, size warning."""

from __future__ import annotations

import errno
import logging
import socket
from pathlib import Path

import pytest

import graph_ted_db.store.graph as graph_mod
from graph_ted_db import GraphStore, init_graph
from graph_ted_db.cli import main as cli_main


@pytest.fixture
def g(tmp_path: Path) -> GraphStore:
    init_graph(tmp_path / "g")
    store = GraphStore.open(tmp_path / "g", data_dir=tmp_path / "d")
    for name, k in (("x", 1), ("y", 1), ("z", 2)):
        store.make_node(labels=["B"], props={"name": name, "k": k})
    return store


def test_count_star(g: GraphStore) -> None:
    assert g.execute("MATCH (b:B) RETURN count(*) AS c") == [{"c": 3}]
    assert g.execute("MATCH (b:Nope) RETURN count(*) AS c") == [{"c": 0}]
    assert g.execute("MATCH (b:B) RETURN b.k AS k, count(*) AS c ORDER BY k") == [
        {"k": 1, "c": 2},
        {"k": 2, "c": 1},
    ]


def test_unaliased_return_columns_use_source_text(g: GraphStore) -> None:
    assert g.execute("MATCH (b:B) RETURN b.name ORDER BY b.name LIMIT 1") == [{"b.name": "x"}]
    assert g.execute("MATCH (b:B) RETURN count(*)") == [{"count(*)": 3}]
    rows = g.execute("MATCH (b:B) RETURN b.k, count(b) ORDER BY b.k")
    assert rows == [{"b.k": 1, "count(b)": 2}, {"b.k": 2, "count(b)": 1}]
    assert g.execute("MATCH (b:B {name: 'x'}) RETURN b.name AS n") == [{"n": "x"}]


def test_temp_files_are_unique_per_process(tmp_path: Path, monkeypatch) -> None:
    import graph_ted_db.store.jsonl as jsonl_mod

    seen: list[str] = []
    real = jsonl_mod.os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        return real(src, dst)

    monkeypatch.setattr(jsonl_mod.os, "replace", spy)
    init_graph(tmp_path / "g")
    store = GraphStore.open(tmp_path / "g", data_dir=tmp_path / "d")
    store.make_node(labels=["A"], props={})
    store.make_node(labels=["C"], props={})
    labels = [n for n in seen if n.startswith("labels.json.")]
    assert len(labels) >= 2 and len(set(labels)) == len(labels)
    assert all(n.endswith(".tmp") and n != "labels.json.tmp" for n in labels)


def test_open_warns_on_large_graphs(g: GraphStore, monkeypatch, caplog) -> None:
    monkeypatch.setattr(graph_mod, "LARGE_GRAPH_RECORDS", 2)
    with caplog.at_level(logging.WARNING, logger="graph_ted_db"):
        GraphStore.open(g.root, data_dir=g.data_dir)
    assert "open took" in caplog.text and "Graph size" in caplog.text


def test_cli_no_graph_message(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "nothing"
    assert cli_main(["ls-nodes", str(missing)]) == 1
    err = capsys.readouterr().err
    assert f"no graph at {missing}; create one with `graph-ted-db init {missing}`" in err


def test_cli_port_in_use_message(tmp_path: Path, capsys, monkeypatch) -> None:
    init_graph(tmp_path / "g")
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "d"))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        rc = cli_main(["serve", str(tmp_path / "g"), "--port", str(port)])
    assert rc == 1
    assert f"port {port} in use; pass --port" in capsys.readouterr().err


def test_serve_warns_off_loopback_even_with_token(g: GraphStore, capsys, monkeypatch) -> None:
    import graph_ted_db.server.http as http_mod

    def stop(*a, **k):
        raise OSError(errno.EADDRNOTAVAIL, "stop here")

    monkeypatch.setattr(http_mod, "make_server", stop)
    with pytest.raises(OSError):
        http_mod.serve(g, host="0.0.0.0", port=0, token="t0ken")
    assert "not loopback" in capsys.readouterr().err
