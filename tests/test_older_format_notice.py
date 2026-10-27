"""An older-format (v2) folder is read normally: an info line, not a problem."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from graph_ted_db.cli import main as cli_main
from graph_ted_db.server.http import _info_payload
from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.graph import OLDER_FORMAT_NOTICE
from graph_ted_db.store.records import NodeRecord

OTHER = "w0123456789abcdef"
NOTICE = (
    "This graph uses an older storage format. It will be upgraded automatically the next"
    " time it's saved. All machines sharing this folder need graph-ted-db 0.1.2 or newer."
)


def _v2_folder(root: Path) -> NodeRecord:
    """A version 2 folder with one record file written by another device."""
    meta = init_graph(root)
    raw = meta.to_dict()
    raw["format_version"] = 2
    raw["layout"] = "per-writer"
    (root / "graph.json").write_text(json.dumps(raw), encoding="utf-8")
    record = NodeRecord(
        id="0f000000-0000-4000-8000-0000000000aa",
        updated_at="2026-08-25T12:00:00.000000Z",
        labels=("E",),
        props={"name": "legacy"},
        writer=OTHER,
    )
    path = root / "nodes" / f"0f.{OTHER}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.to_jsonl() + "\n", encoding="utf-8")
    return record


def test_notice_text_is_the_documented_one() -> None:
    assert OLDER_FORMAT_NOTICE == NOTICE


def test_v2_folder_opens_with_info_not_a_warning(tmp_path: Path, caplog) -> None:
    root = tmp_path / "g"
    record = _v2_folder(root)
    with caplog.at_level(logging.INFO, logger="graph_ted_db"):
        store = GraphStore.open(root, data_dir=tmp_path / "d")
    assert store.get_node(record.id) is not None
    assert store.problems() == dict.fromkeys(store.problems(), 0)
    assert "legacy_v2_files" not in store.problems()
    assert store.notices() == {"legacy_v2_files": 1, "older_format": 1}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(NOTICE in m for m in infos)
    assert "doctor" not in caplog.text
    payload = _info_payload(store)
    assert set(payload["problems"].values()) == {0}
    assert payload["notices"]["legacy_v2_files"] == 1


def test_current_format_has_no_notice(tmp_path: Path, caplog) -> None:
    root = tmp_path / "g"
    init_graph(root)
    with caplog.at_level(logging.INFO, logger="graph_ted_db"):
        store = GraphStore.open(root, data_dir=tmp_path / "d")
    assert store.notices() == {"legacy_v2_files": 0, "older_format": 0}
    assert not store.uses_older_format()
    assert NOTICE not in caplog.text


def test_cli_info_check_and_doctor_treat_v2_as_info(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "g"
    _v2_folder(root)
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "d"))

    assert cli_main(["info", "--check", str(root)]) == 0
    out, err = capsys.readouterr()
    assert "legacy_v2_files" not in out.splitlines()[1]
    assert "info: legacy_v2_files=1 older_format=1" in out
    assert NOTICE in err
    assert "problems" not in err
    assert "run `graph-ted-db doctor`" not in err

    assert cli_main(["doctor", str(root)]) == 0
    out, err = capsys.readouterr()
    assert "v2 files not sealed yet" not in out
    assert "info: older-format (v2) files, read normally: 1" in out
    assert f"info: {NOTICE}" in out
    assert "run `graph-ted-db doctor`" not in err


def test_real_problem_still_warns_next_to_the_info(tmp_path: Path, monkeypatch, caplog, capsys) -> None:
    root = tmp_path / "g"
    _v2_folder(root)
    (root / "nodes" / f"0e.{OTHER}.jsonl").write_text('{"not": "a record"}\n', encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="graph_ted_db"):
        store = GraphStore.open(root, data_dir=tmp_path / "d")
    assert store.problems()["skipped_lines"] == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("opened with problems" in m and "run `graph-ted-db doctor`" in m for m in warnings)
    assert not any("legacy_v2_files" in m for m in warnings)
    assert NOTICE in caplog.text

    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "d"))
    assert cli_main(["info", "--check", str(root)]) == 1
    out, err = capsys.readouterr()
    assert "skipped_lines=1" in out
    assert "run `graph-ted-db doctor`" in err


def test_cli_leaves_logging_as_it_was(tmp_path: Path, monkeypatch) -> None:
    lib = logging.getLogger("graph_ted_db")
    before = (list(lib.handlers), lib.level, lib.propagate)
    root = tmp_path / "g"
    _v2_folder(root)
    monkeypatch.setenv("GRAPH_TED_DB_DATA", str(tmp_path / "d"))
    assert cli_main(["ls-nodes", str(root)]) == 0
    assert (list(lib.handlers), lib.level, lib.propagate) == before


@pytest.mark.parametrize("write", [False, True])
def test_reading_keeps_format_and_writing_upgrades(tmp_path: Path, write: bool) -> None:
    root = tmp_path / "g"
    _v2_folder(root)
    store = GraphStore.open(root, data_dir=tmp_path / "d")
    if write:
        store.make_node(labels=["E"], props={"name": "new"})
    reopened = GraphStore.open(root, data_dir=tmp_path / "d")
    assert reopened.notices()["older_format"] == (0 if write else 1)
    # Another device's v2 file stays until that device opens the folder.
    assert reopened.notices()["legacy_v2_files"] == 1
