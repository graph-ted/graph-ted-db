import json
from pathlib import Path

from graph_ted_db.cli import main


def test_cli_put_ls_get_delete(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "data"))
    root = tmp_path / "graph"
    assert main(["init", str(root), "--name", "cli"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "put-node",
                str(root),
                "--label",
                "Entity",
                "--prop",
                "name=Alice",
                "--by",
                "tester",
            ]
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["props"]["name"] == "Alice"
    assert created["labels"] == ["Entity"]
    node_id = created["id"]

    assert main(["ls-nodes", str(root)]) == 0
    listed = capsys.readouterr().out.strip().splitlines()
    assert len(listed) == 1

    assert main(["get-node", str(root), node_id]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["id"] == node_id

    assert (
        main(
            [
                "put-edge",
                str(root),
                "--type",
                "KNOWS",
                "--from",
                node_id,
                "--to",
                node_id,
                "--by",
                "tester",
            ]
        )
        == 0
    )
    edge = json.loads(capsys.readouterr().out)
    assert main(["ls-edges", str(root)]) == 0
    assert capsys.readouterr().out.strip()

    assert main(["delete-node", str(root), node_id]) == 0
    capsys.readouterr()
    assert main(["get-node", str(root), node_id]) == 1
    assert main(["delete-edge", str(root), edge["id"]]) == 0

    assert main(["doctor", str(root)]) == 0
    doctor_out = capsys.readouterr().out
    assert "torn lines repaired" in doctor_out

    assert (
        main(
            [
                "cypher",
                str(root),
                "MATCH (n) RETURN count(n) AS n",
            ]
        )
        == 0
    )
    # node was deleted above; count may be 0
    capsys.readouterr()


def test_serve_off_loopback_requires_token(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("GRAPH_TED_DB_TOKEN", raising=False)
    root = tmp_path / "graph"
    assert main(["init", str(root), "--name", "cli"]) == 0
    capsys.readouterr()
    assert main(["serve", str(root), "--host", "0.0.0.0"]) == 1
    err = capsys.readouterr().err
    assert "token" in err.lower()


def test_serve_off_loopback_passes_token(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("GRAPH_TED_DB_TOKEN", raising=False)
    root = tmp_path / "graph"
    assert main(["init", str(root), "--name", "cli"]) == 0
    capsys.readouterr()
    called = {}

    def fake_serve(store, host="127.0.0.1", port=8099, *, max_records=500, token=""):
        called["host"] = host
        called["token"] = token

    monkeypatch.setattr("graph_ted_db.server.serve", fake_serve)
    assert main(["serve", str(root), "--host", "0.0.0.0", "--token", "s3cret"]) == 0
    assert called == {"host": "0.0.0.0", "token": "s3cret"}

    monkeypatch.setenv("GRAPH_TED_DB_TOKEN", "from-env")
    assert main(["serve", str(root), "--host", "0.0.0.0"]) == 0
    assert called["token"] == "from-env"
