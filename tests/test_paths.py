from pathlib import Path

from graph_ted_db.store.paths import (
    GraphPaths,
    bundle_filename,
    bundle_jsonl_files,
    is_bundle_name,
    is_canonical_shard_name,
    is_conflict_copy,
    is_legacy_v2_name,
    is_per_shard_segment_name,
    is_sealed_segment_name,
    segment_filename,
    segment_seq,
    shard_jsonl_files,
    writer_of,
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


def test_segment_names_carry_one_writer_and_a_sequence():
    sealed = segment_filename("0f", "w0123456789abcdef", 1)
    assert sealed == "0f.w0123456789abcdef.000001.jsonl"
    assert is_sealed_segment_name(sealed)
    assert not is_legacy_v2_name(sealed)
    assert segment_seq(sealed) == 1
    assert writer_of(sealed) == "w0123456789abcdef"
    legacy = "0f.w0123456789abcdef.jsonl"
    assert is_legacy_v2_name(legacy)
    assert not is_sealed_segment_name(legacy)
    assert writer_of(legacy) == "w0123456789abcdef"
    assert not is_conflict_copy(Path(sealed), "0f")
    assert is_conflict_copy(Path(sealed + ".conflict1"), "0f")


def test_shard_jsonl_files_include_segments_and_legacy(tmp_path: Path):
    nodes = tmp_path / "nodes"
    nodes.mkdir()
    (nodes / "00.jsonl").write_text("{}\n", encoding="utf-8")
    (nodes / "00.w0123456789abcdef.jsonl").write_text("{}\n", encoding="utf-8")
    (nodes / "00.w0123456789abcdef.000003.jsonl").write_text("{}\n", encoding="utf-8")
    (nodes / "00.w0123456789abcdef.000003.jsonl.conflict1").write_text("{}\n", encoding="utf-8")
    names = {path.name for path in shard_jsonl_files(nodes, "00")}
    assert names == {
        "00.jsonl",
        "00.w0123456789abcdef.jsonl",
        "00.w0123456789abcdef.000003.jsonl",
        "00.w0123456789abcdef.000003.jsonl.conflict1",
    }


def test_bundle_names_cover_every_shard(tmp_path: Path):
    writer = "w0123456789abcdef"
    name = bundle_filename(writer, 1)
    assert name == "w0123456789abcdef.000001.jsonl"
    assert is_bundle_name(name)
    assert is_sealed_segment_name(name)
    assert not is_per_shard_segment_name(name)
    assert not is_legacy_v2_name(name)
    assert segment_seq(name) == 1
    assert writer_of(name) == writer
    nodes = tmp_path / "nodes"
    nodes.mkdir()
    per_shard = "00.w0123456789abcdef.000001.jsonl"
    (nodes / name).write_text("{}\n", encoding="utf-8")
    (nodes / f"{name}.conflict1").write_text("{}\n", encoding="utf-8")
    (nodes / per_shard).write_text("{}\n", encoding="utf-8")
    assert {path.name for path in bundle_jsonl_files(nodes)} == {name, f"{name}.conflict1"}
    shard_names = {path.name for path in shard_jsonl_files(nodes, "00")}
    assert per_shard in shard_names
    assert name not in shard_names


def test_graph_paths_shard_from_uuid(tmp_path: Path):
    paths = GraphPaths(tmp_path)
    assert paths.node_shard("550e8400-e29b-41d4-a716-446655440000").name == "55.jsonl"
    assert (
        paths.vector_shard("name_embedding", "550e8400-e29b-41d4-a716-446655440000")
        == tmp_path / "vectors" / "name_embedding" / "55.jsonl"
    )
