from pathlib import Path

from graph_ted_db.store.paths import (
    GraphPaths,
    is_canonical_shard_name,
    is_conflict_copy,
    shard_jsonl_files,
)


def test_canonical_shard_names():
    assert is_canonical_shard_name("00.jsonl")
    assert is_canonical_shard_name("ff.jsonl")
    assert not is_canonical_shard_name("00.JSONL")
    assert not is_canonical_shard_name("0.jsonl")
    assert not is_canonical_shard_name("gg.jsonl")


def test_conflict_copy_patterns():
    assert is_conflict_copy(Path("00-DESKTOP-NAME-conflict-2026-08-25.jsonl"), "00")
    assert is_conflict_copy(Path("00 (conflicted copy).jsonl"), "00")
    assert is_conflict_copy(Path("00-conflict-copy.jsonl"), "00")
    assert not is_conflict_copy(Path("00.jsonl"), "00")
    assert not is_conflict_copy(Path("01.jsonl"), "00")
    assert not is_conflict_copy(Path("00.jsonl.bak"), "00")
    assert not is_conflict_copy(Path("00.jsonl.tmp"), "00")
    assert not is_conflict_copy(Path("~$00.jsonl"), "00")


def test_shard_jsonl_files_unions_conflicts(tmp_path: Path):
    nodes = tmp_path / "nodes"
    nodes.mkdir()
    (nodes / "00.jsonl").write_text("{}\n", encoding="utf-8")
    (nodes / "00-laptop-conflict.jsonl").write_text("{}\n", encoding="utf-8")
    (nodes / "01.jsonl").write_text("{}\n", encoding="utf-8")
    files = shard_jsonl_files(nodes, "00")
    names = {p.name for p in files}
    assert names == {"00.jsonl", "00-laptop-conflict.jsonl"}


def test_graph_paths_shard_from_uuid(tmp_path: Path):
    paths = GraphPaths(tmp_path)
    assert paths.node_shard("550e8400-e29b-41d4-a716-446655440000").name == "55.jsonl"
    assert (
        paths.vector_shard("name_embedding", "550e8400-e29b-41d4-a716-446655440000")
        == tmp_path / "vectors" / "name_embedding" / "55.jsonl"
    )
