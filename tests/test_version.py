"""The version string is defined twice; keep them in step."""

from __future__ import annotations

import re
from pathlib import Path

import graph_ted_db
from graph_ted_db.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, "no version in pyproject.toml"
    return match.group(1)


def test_dunder_version_matches_pyproject() -> None:
    assert graph_ted_db.__version__ == _pyproject_version()


def test_changelog_has_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{graph_ted_db.__version__}]" in changelog


def test_cli_version(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out.strip() == f"graph-ted-db {graph_ted_db.__version__}"
