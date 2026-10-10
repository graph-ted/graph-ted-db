"""App metadata files: meta/<namespace>/<name>.<writer>.json (one file per writer)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import graph_ted_db.store.graph as graph_mod
import graph_ted_db.store.jsonl as jsonl_mod
from graph_ted_db import GraphStore, init_graph
from graph_ted_db.store.meta import MetaPathError, parse_name


def _two_devices(tmp_path: Path) -> tuple[GraphStore, GraphStore]:
    root = tmp_path / "g"
    init_graph(root)
    a = GraphStore(root, data_dir=tmp_path / "app-a")
    b = GraphStore(root, data_dir=tmp_path / "app-b")
    assert a.writer_id != b.writer_id
    return a, b


def test_put_and_list_round_trip(tmp_path):
    a, _ = _two_devices(tmp_path)
    p = a.meta_put("ontology/default", "v1", {"version": 1, "name": "Café"})
    assert p == a.root / "meta" / "ontology" / "default" / f"v1.{a.writer_id}.json"
    assert p.read_bytes().endswith(b"\n")
    [e] = a.meta_list("ontology/default")
    assert (e.name, e.writer, e.data, e.problem) == (
        "v1",
        a.writer_id,
        {"version": 1, "name": "Café"},
        None,
    )
    assert a.meta_list("ontology/nothing-here") == []


def test_concurrent_publishers_from_the_same_base_both_keep_their_file(tmp_path):
    a, b = _two_devices(tmp_path)
    a.meta_put("ontology/default", "v2", {"by": "a", "parent": 1})
    b.meta_put("ontology/default", "v2", {"by": "b", "parent": 1})
    got = {(e.name, e.writer): e.data["by"] for e in a.meta_list("ontology/default")}
    assert got == {("v2", a.writer_id): "a", ("v2", b.writer_id): "b"}
    # A sync client that copies a whole folder over another keeps both files too.
    other = tmp_path / "copy"
    shutil.copytree(a.root, other)
    assert len(list((other / "meta" / "ontology" / "default").iterdir())) == 2


def test_versions_are_append_only_by_default_pointers_may_be_replaced(tmp_path):
    a, _ = _two_devices(tmp_path)
    a.meta_put("ontology/default", "v1", {"n": 1})
    with pytest.raises(FileExistsError):
        a.meta_put("ontology/default", "v1", {"n": 2})
    a.meta_put("ontology/default", "head", {"version": 1})
    a.meta_put("ontology/default", "head", {"version": 2}, replace_existing=True)
    heads = [e.data for e in a.meta_list("ontology/default") if e.name == "head"]
    assert heads == [{"version": 2}]


def test_write_is_atomic(tmp_path, monkeypatch):
    a, _ = _two_devices(tmp_path)
    a.meta_put("ns", "head", {"version": 1})

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(jsonl_mod.os, "replace", boom)
    with pytest.raises(OSError, match="No space"):
        a.meta_put("ns", "head", {"version": 2}, replace_existing=True)
    monkeypatch.undo()
    [e] = a.meta_list("ns")
    assert e.data == {"version": 1}  # old content intact, no temp left behind
    assert not list((a.root / "meta" / "ns").glob("*.tmp"))


@pytest.mark.parametrize(
    ("directory", "name"),
    [
        ("../x", "v1"),
        ("/abs", "v1"),
        ("writers", "v1"),
        ("deleted", "v1"),
        ("labels.json", "v1"),
        ("ok", "../v1"),
        ("ok", "a/b"),
        ("", "v1"),
        ("ok", ".hidden"),
        ("ok", "v1.tmp"),
    ],
)
def test_unsafe_or_reserved_paths_are_rejected(tmp_path, directory, name):
    a, _ = _two_devices(tmp_path)
    with pytest.raises(MetaPathError):
        a.meta_put(directory, name, {})


def test_only_objects_are_stored(tmp_path):
    a, _ = _two_devices(tmp_path)
    with pytest.raises(TypeError):
        a.meta_put("ns", "x", [1, 2])  # type: ignore[arg-type]


def test_registers_the_writer_like_a_record_write(tmp_path):
    a, _ = _two_devices(tmp_path)
    assert a.writer_id not in a.writers()
    a.meta_put("ns", "x", {})
    assert a.writer_id in a.writers()


def test_problem_files_are_reported_not_hidden_not_modified(tmp_path):
    a, b = _two_devices(tmp_path)
    a.meta_put("ontology/default", "v1", {"ok": True})
    d = a.root / "meta" / "ontology" / "default"
    (d / f"v2.{b.writer_id}.json").write_bytes(b'{"half": ')  # partial download
    (d / f"v3.{b.writer_id}.json").write_bytes(b"")
    (d / f"v4.{b.writer_id}.json").write_bytes(b"[1]\n")
    (d / f"v1.{a.writer_id} (1).json").write_bytes(b"{}\n")  # sync conflict copy
    (d / "v5.json.123.tmp").write_bytes(b"{")
    before = {p.name: p.read_bytes() for p in d.iterdir()}

    problems = {e.path.name: e.problem for e in a.meta_list("ontology/default")}
    assert problems == {
        f"v1.{a.writer_id}.json": None,
        f"v2.{b.writer_id}.json": "unterminated",
        f"v3.{b.writer_id}.json": "empty",
        f"v4.{b.writer_id}.json": "invalid-json",
        f"v1.{a.writer_id} (1).json": "conflict-copy",
        "v5.json.123.tmp": "tmp",
    }

    fresh = GraphStore(a.root, data_dir=tmp_path / "app-a")
    assert fresh.problems()["meta_problems"] == 5
    report = fresh.doctor()
    assert len(report.meta_problems) == 5
    assert "app meta files with problems (not modified): 5" in report.summary()
    assert any(x.startswith("unterminated meta/ontology/default/v2.") for x in report.meta_problems)
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before


def test_cloud_only_meta_file_is_reported(tmp_path, monkeypatch):
    a, _ = _two_devices(tmp_path)
    p = a.meta_put("ns", "v1", {"x": 1})
    real = graph_mod.is_cloud_only
    monkeypatch.setattr(
        graph_mod, "is_cloud_only", lambda st: st.st_ino == os.stat(p).st_ino or real(st)
    )
    [e] = a.meta_list("ns")
    assert e.problem == "cloud-only" and e.data is None
    assert a.doctor().meta_problems == [f"cloud-only meta/ns/{p.name}"]


def test_healthy_meta_and_store_owned_meta_are_not_problems(tmp_path):
    a, _ = _two_devices(tmp_path)
    a.meta_put("ontology/default", "v1", {})
    a.meta_put("ontology/other-group", "v1", {})
    assert a.problems()["meta_problems"] == 0
    assert a.doctor().meta_problems == []


def test_parse_name():
    w = "w0123456789abcdef"
    assert parse_name(f"v12.{w}.json") == ("v12", w)
    assert parse_name(f"head.{w}.json") == ("head", w)
    assert parse_name("v12.json") is None
    assert parse_name(f"v1.{w} (1).json") is None
    json.dumps(parse_name(f"a.b.{w}.json"))
