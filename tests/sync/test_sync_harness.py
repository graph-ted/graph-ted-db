"""Smoke test for the two-writer file-sync harness. Skipped by default.

Set GTDB_SYNC_TESTS=1 and have rclone on PATH (or GTDB_SYNC_RCLONE) to run it.
It uses a temporary local directory as the remote.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RCLONE = os.environ.get("GTDB_SYNC_RCLONE") or shutil.which("rclone")

pytestmark = pytest.mark.skipif(
    os.environ.get("GTDB_SYNC_TESTS") != "1" or not RCLONE,
    reason="file-sync harness: set GTDB_SYNC_TESTS=1 and install rclone",
)


def _harness():
    spec = importlib.util.spec_from_file_location(
        "sync_harness", ROOT / "scripts" / "sync_test" / "sync_harness.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_harness"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_sequential_and_conflict_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = _harness()
    monkeypatch.setenv("GTDB_SYNC_REMOTE", str(tmp_path / "remote"))
    monkeypatch.setenv("GTDB_SYNC_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("GTDB_SYNC_RCLONE", str(RCLONE))
    (tmp_path / "remote").mkdir()
    cfg = h.Config.from_env()
    res = h.sc1_sequential(cfg)
    assert res["verdict"]["clean"], res
    names = h.sc8_native_conflict_names(cfg)["results"]
    assert all(v["record_visible"] for v in names.values()), names
