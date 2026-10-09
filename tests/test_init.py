import json
from pathlib import Path

import pytest

from graph_ted_db.cli import main
from graph_ted_db.store import GraphFormatError, init_graph, load_graph_meta
from graph_ted_db.store.paths import GraphPaths


def test_init_graph_creates_layout_without_preallocating_shards(tmp_path: Path):
    root = tmp_path / "g"
    meta = init_graph(root, name="demo")
    paths = GraphPaths(root)
    assert paths.graph_json.is_file()
    assert paths.writers_dir.is_dir()
    assert not paths.deleted_jsonl.exists()  # v2: tombstone files are per writer
    assert paths.labels_json.is_file()
    for d in paths.required_directories():
        assert d.is_dir()
    # Do not pre-create 256 empty shard files.
    assert list(paths.nodes_dir.glob("*.jsonl")) == []
    assert list(paths.edges_dir.glob("*.jsonl")) == []
    loaded = load_graph_meta(root)
    assert loaded.id == meta.id
    assert loaded.name == "demo"
    assert loaded.format == "graph-ted-db"
    assert loaded.format_version == 2
    assert loaded.extras["layout"] == "per-writer"
    assert loaded.shard_fanout == 256


def test_init_graph_refuses_to_clobber(tmp_path: Path):
    root = tmp_path / "g"
    first = init_graph(root, name="a")
    with pytest.raises(FileExistsError):
        init_graph(root, name="b")
    same = init_graph(root, name="b", exist_ok=True)
    assert same.id == first.id
    assert same.name == "a"


def test_load_graph_preserves_unknown_graph_json_fields(tmp_path: Path):
    root = tmp_path / "g"
    init_graph(root, name="demo")
    path = GraphPaths(root).graph_json
    data = json.loads(path.read_text(encoding="utf-8"))
    data["embedder_model"] = "test-embedder"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    loaded = load_graph_meta(root)
    assert loaded.extras["embedder_model"] == "test-embedder"
    assert loaded.to_dict()["embedder_model"] == "test-embedder"


def test_load_missing_graph(tmp_path: Path):
    with pytest.raises(GraphFormatError):
        load_graph_meta(tmp_path)


def test_cli_init_and_info(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    root = tmp_path / "cli-graph"
    assert main(["init", str(root), "--name", "from-cli"]) == 0
    assert main(["info", str(root)]) == 0
    out = capsys.readouterr().out
    assert "from-cli" in out
    data = json.loads((root / "graph.json").read_text(encoding="utf-8"))
    assert data["name"] == "from-cli"
