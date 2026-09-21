from pathlib import Path

from graph_ted_db.store.jsonl import append_jsonl, repair_torn_jsonl, replace_json_file
from graph_ted_db.store.records import NodeRecord


def test_repair_torn_jsonl_truncates_incomplete_tail(tmp_path: Path):
    path = tmp_path / "00.jsonl"
    complete = '{"id":"a"}\n'
    torn = '{"id":"b"'
    path.write_bytes((complete + torn).encode("utf-8"))
    discarded = repair_torn_jsonl(path)
    assert discarded == len(torn)
    assert path.read_bytes() == complete.encode("utf-8")


def test_repair_torn_jsonl_empty_when_no_newline(tmp_path: Path):
    path = tmp_path / "00.jsonl"
    path.write_bytes(b'{"id":"only-torn"')
    discarded = repair_torn_jsonl(path)
    assert discarded > 0
    assert path.read_bytes() == b""


def test_repair_is_noop_when_newline_terminated(tmp_path: Path):
    path = tmp_path / "00.jsonl"
    path.write_bytes(b'{"id":"a"}\n')
    assert repair_torn_jsonl(path) == 0
    assert path.read_bytes() == b'{"id":"a"}\n'


def test_append_after_torn_tail_does_not_fuse_records(tmp_path: Path):
    path = tmp_path / "00.jsonl"
    first = NodeRecord(
        id="00000000-0000-4000-8000-000000000001",
        updated_at="2026-08-25T12:00:00.000000Z",
        labels=("Entity",),
        props={"name": "first"},
    )
    path.write_bytes((first.to_jsonl() + "\n" + '{"id":"torn"').encode("utf-8"))
    second = NodeRecord(
        id="00000000-0000-4000-8000-000000000002",
        updated_at="2026-08-25T12:00:01.000000Z",
        labels=("Entity",),
        props={"name": "second"},
    )
    append_jsonl(path, second.to_jsonl())
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    lines = [line for line in text.splitlines() if line]
    assert len(lines) == 2
    assert "first" in lines[0]
    assert "second" in lines[1]
    assert "torn" not in text


def test_replace_json_file_is_valid_json(tmp_path: Path):
    path = tmp_path / "graph.json"
    replace_json_file(path, {"format": "graph-ted-db", "name": "x"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"name": "x"' in path.read_text(encoding="utf-8")
