"""The PII scan flags personal sync paths, links, home paths, and local terms.

All values here are synthetic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pii_scan", ROOT / "scripts" / "pii_scan.py")
pii = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["pii_scan"] = pii
_spec.loader.exec_module(pii)


def rules_hit(text: str, rules=None) -> set[str]:
    return {f.rule for f in pii.scan_text("x.md", text, rules or pii.GENERIC_RULES)}


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("see https://onedrive.live.com/?id=ABC", "onedrive-share-link"),
        ("short link https://1drv.ms/f/s!abc", "onedrive-share-link"),
        (
            "https://contoso-my.sharepoint.com/personal/jdoe_contoso_com/Documents",
            "sharepoint-personal-path",
        ),
        ("graph at ~/OneDrive/graph-ted/graph", "onedrive-local-path"),
        (r"D:\OneDrive - Contoso\graph", "onedrive-local-path"),
        ("mounted at /mnt/onedrive-jdoe/graph", "onedrive-mount-path"),
        ("cd /home/jdoe/projects", "home-path-unix"),
        ("open /Users/jdoe/Documents", "home-path-mac"),
        (r"C:\Users\jdoe\graph", "home-path-windows"),
        ("mail jane.doe@gmail.com", "personal-email"),
        ('{"access_token":"EwBwA8l6BAAU1234567"}', "rclone-config-token"),
        ("drive_id = 0123456789abcdef", "rclone-drive-id"),
    ],
)
def test_generic_rules_flag(text: str, rule: str) -> None:
    assert rule in rules_hit(text)


@pytest.mark.parametrize(
    "text",
    [
        "A sync client (OneDrive, rclone, abraunegg) may fork these files.",
        "## OneDrive conflict copies",
        "Empty files still count against OneDrive's item budget.",
        "nodes/00-DESKTOP-NAME-conflict-2026-08-25.jsonl",
        "contact developer.agent@graph-ted.com",
        "cd /home/runner/work and $HOME/.cache",
        r"C:\Users\<user>\graph",
        "rclone bisync ./graph $GTDB_SYNC_REMOTE:graph-ted-sync-test/store",
    ],
)
def test_generic_rules_allow(text: str) -> None:
    assert rules_hit(text) == set()


def test_local_terms_from_env_are_reported_by_index_only() -> None:
    rules = pii.load_local_rules(
        root=Path("/nonexistent"),
        env={
            "GTDB_PII_TERMS": "Jane Placeholder,jane@example.org",
            "GTDB_PII_REMOTES": "examplecloud",
        },
    )
    findings = pii.scan_text(
        "notes.md",
        "by jane placeholder\nrclone lsf examplecloud:graph-ted-sync-test\nexamplecloudy: fine\n",
        rules,
    )
    assert [(f.line, f.rule) for f in findings] == [(1, "local-term#1"), (2, "local-remote#1")]
    assert all(
        "placeholder" not in str(f).lower() and "examplecloud" not in str(f) for f in findings
    )


def test_local_terms_file(tmp_path: Path) -> None:
    (tmp_path / pii.LOCAL_TERMS_FILE).write_text(
        "# comment\nremote:mycloud\nterm:someone@example.net\n"
    )
    rules = pii.load_local_rules(root=tmp_path, env={})
    hits = {f.rule for f in pii.scan_text("a.txt", "mycloud:x\nSomeone@Example.net\n", rules)}
    assert hits == {"local-remote#1", "local-term#1"}


def test_repository_is_clean() -> None:
    findings = pii.scan_files(pii.tracked_files(ROOT), pii.GENERIC_RULES, ROOT)
    assert findings == [], [str(f) for f in findings]


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _history_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Example Bot")
    _git(repo, "config", "user.email", "bot@example.com")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_pii_scan.py").write_text("FIXTURE = r'C:\\Users\\jdoe\\graph'\n")
    (repo / "README.md").write_text("A clean readme.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_history_skips_own_fixtures(tmp_path: Path) -> None:
    repo = _history_repo(tmp_path)
    assert pii.scan_history(pii.GENERIC_RULES, root=repo) == []


def test_history_still_flags_other_files_and_messages(tmp_path: Path) -> None:
    repo = _history_repo(tmp_path)
    (repo / "docs.md").write_text("cd /home/jdoe/projects\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mail jane.doe@gmail.com")
    rules = {f.rule for f in pii.scan_history(pii.GENERIC_RULES, root=repo)}
    assert rules == {"home-path-unix", "personal-email"}


def test_history_applies_local_terms_to_own_fixtures(tmp_path: Path) -> None:
    repo = _history_repo(tmp_path)
    rules = pii.load_local_rules(root=repo, env={"GTDB_PII_TERMS": "jdoe"})
    assert {f.rule for f in pii.scan_history(rules, root=repo)} == {"local-term#1"}


def test_store_files_and_writer_ids_have_no_pii(tmp_path: Path) -> None:
    """Writer ids and every file a store writes pass the scan (format v2)."""
    from graph_ted_db import GraphStore, init_graph
    from graph_ted_db.store.format import is_writer_id

    root = tmp_path / "g"
    init_graph(root, name="demo")
    data = tmp_path / "appdata"
    g = GraphStore.open(root, data_dir=data)
    a = g.make_node(labels=["E"], props={"name": "a"})
    b = g.make_node(labels=["E"], props={"name": "b"})
    g.make_edge(type="R", from_id=a.id, to_id=b.id)
    g.delete_node(b.id)
    assert is_writer_id(g.writer_id)
    findings = []
    for path in [*root.rglob("*"), *data.rglob("*")]:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            findings += pii.scan_text(path.name + " " + path.name, text, pii.GENERIC_RULES)
            findings += pii.scan_text("name", path.name, pii.GENERIC_RULES)
    assert findings == []
