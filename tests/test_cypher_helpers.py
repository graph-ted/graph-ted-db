"""Run graph-ted helper Cypher against a Graphiti-shaped graph-ted-db folder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from graph_ted_db.store import GraphStore, init_graph

# Default: sibling checkout
#   <parent>/graph-ted-db/
#   <parent>/graph-ted/backend/cypher/helpers/
# Override with env HELPER_DIR (absolute or relative path).
_DEFAULT_HELPER_DIR = (
    Path(__file__).resolve().parents[2]
    / "graph-ted"
    / "backend"
    / "cypher"
    / "helpers"
)
HELPER_DIR = Path(os.environ.get("HELPER_DIR", str(_DEFAULT_HELPER_DIR))).expanduser().resolve()

ALICE = "00000000-0000-4000-8000-0000000000a1"
FORM = "00000000-0000-4000-8000-0000000000f1"
QUEST = "00000000-0000-4000-8000-0000000000c1"
EP = "00000000-0000-4000-8000-0000000000e1"
HAS_FORM = "00000000-0000-4000-8000-000000000011"
ANSWERS = "00000000-0000-4000-8000-000000000012"
MENTIONS_A = "00000000-0000-4000-8000-000000000013"
MENTIONS_F = "00000000-0000-4000-8000-000000000014"
MENTIONS_Q = "00000000-0000-4000-8000-000000000015"


def _helper(name: str) -> str:
    path = HELPER_DIR / f"{name}.cypher"
    if not path.is_file():
        pytest.skip(f"graph-ted helper not at {path}")
    return path.read_text(encoding="utf-8")


def _entity(g: GraphStore, record_id: str, labels: list[str], **props) -> None:
    payload = {
        "uuid": record_id,
        "group_id": "default",
        "created_at": "2026-01-01T00:00:00.000000Z",
        **props,
    }
    g.make_node(record_id=record_id, labels=labels, props=payload)


def _rel(
    g: GraphStore,
    record_id: str,
    type: str,
    from_id: str,
    to_id: str,
    **props,
) -> None:
    payload = {
        "uuid": record_id,
        "group_id": "default",
        "created_at": "2026-01-02T00:00:00.000000Z",
        **props,
    }
    g.make_edge(type=type, from_id=from_id, to_id=to_id, props=payload, record_id=record_id)


def _seed(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="helpers")
    g = GraphStore.open(root, data_dir=tmp_path / "data")
    _entity(g, ALICE, ["Entity", "Person"], name="Alice", summary="buyer")
    _entity(
        g,
        FORM,
        ["Entity", "Form"],
        name="Field process",
        attributes={"form_key": "fp"},
    )
    _entity(g, QUEST, ["Entity", "Question"], name="Budget?")
    _entity(
        g,
        EP,
        ["Episodic"],
        name="call-1",
        content="Alice called",
        source_description="note",
    )
    _rel(g, HAS_FORM, "RELATES_TO", ALICE, FORM, name="HAS_FORM", fact="has form")
    _rel(
        g,
        ANSWERS,
        "RELATES_TO",
        ALICE,
        QUEST,
        name="ANSWERS",
        fact="yes",
        attributes={"is_answer": True},
        valid_at="2026-01-03T00:00:00.000000Z",
    )
    _rel(g, MENTIONS_A, "MENTIONS", EP, ALICE)
    _rel(g, MENTIONS_F, "MENTIONS", EP, FORM)
    _rel(g, MENTIONS_Q, "MENTIONS", EP, QUEST)
    return g


def test_get_entity_focus(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(_helper("get_entity_focus"), {"uuid": ALICE, "group_id": "default"})
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert "Person" in rows[0]["entity_types"]
    assert rows[0]["uuid"] == ALICE


def test_get_episode_focus(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(_helper("get_episode_focus"), {"uuid": EP, "group_id": "default"})
    assert rows[0]["content"] == "Alice called"
    assert rows[0]["source_description"] == "note"


def test_list_default_group_entities_hides_forms(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(_helper("list_default_group_entities"), {"group_id": "default"})
    names = {r["name"] for r in rows}
    assert names == {"Alice", "Budget?"}
    alice = next(r for r in rows if r["uuid"] == ALICE)
    assert any(f["uuid"] == FORM for f in alice["forms"])


def test_get_entity_related(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(
        _helper("get_entity_related_entities"), {"uuid": ALICE, "group_id": "default"}
    )
    by_name = {r["name"]: r for r in rows}
    assert "Field process" in by_name
    assert by_name["Field process"]["edge_name"] == "HAS_FORM"
    assert "Budget?" not in by_name  # Question excluded


def test_get_entity_questions_and_forms(tmp_path: Path):
    g = _seed(tmp_path)
    questions = g.execute(
        _helper("get_entity_questions"), {"uuid": ALICE, "group_id": "default"}
    )
    assert [q["name"] for q in questions] == ["Budget?"]
    forms = g.execute(_helper("get_entity_forms"), {"uuid": ALICE, "group_id": "default"})
    assert [f["name"] for f in forms] == ["Field process"]


def test_get_entity_episodes(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(_helper("get_entity_episodes"), {"uuid": ALICE, "group_id": "default"})
    assert len(rows) == 1
    assert rows[0]["uuid"] == EP
    assert ALICE in rows[0]["mentioned_entity_uuids"]
    assert rows[0]["exclusive"] is False


def test_get_entity_answers(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(
        _helper("get_entity_answers"),
        {"uuids": [QUEST], "group_id": "default"},
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert rows[0]["target_uuid"] == QUEST
    assert rows[0]["fact"] == "yes"


def test_episode_helpers(tmp_path: Path):
    g = _seed(tmp_path)
    params = {"uuid": EP, "group_id": "default"}
    related = g.execute(_helper("get_episode_related_entities"), params)
    names = {r["name"] for r in related}
    assert "Alice" in names
    assert "Budget?" not in names
    questions = g.execute(_helper("get_episode_questions"), params)
    assert [q["name"] for q in questions] == ["Budget?"]
    forms = g.execute(_helper("get_episode_forms"), params)
    assert [f["name"] for f in forms] == ["Field process"]


def test_list_entities_related_to_focus(tmp_path: Path):
    g = _seed(tmp_path)
    rows = g.execute(
        _helper("list_entities_related_to_focus"),
        {"focus_uuid": ALICE, "group_id": "default"},
    )
    names = {r["name"] for r in rows}
    assert "Alice" in names
    assert "Budget?" in names
    assert "Field process" not in names


def test_get_episode_linked_episodes(tmp_path: Path):
    g = _seed(tmp_path)
    ep2 = "00000000-0000-4000-8000-0000000000e2"
    _entity(g, ep2, ["Episodic"], name="call-2", content="follow-up")
    _rel(
        g,
        "00000000-0000-4000-8000-000000000016",
        "MENTIONS",
        ep2,
        ALICE,
    )
    rows = g.execute(
        _helper("get_episode_linked_episodes"), {"uuid": EP, "group_id": "default"}
    )
    assert [r["uuid"] for r in rows] == [ep2]
    assert ALICE in rows[0]["mentioned_entity_uuids"]


def test_delete_helpers(tmp_path: Path):
    g = _seed(tmp_path)
    deleted = g.execute(
        _helper("delete_entity_edge"),
        {"uuid": ANSWERS, "group_id": "default"},
    )
    assert deleted[0]["deleted"] is True
    assert g.get_edge(ANSWERS) is None
    gone = g.execute(
        _helper("delete_entity_node"),
        {"uuid": ALICE, "group_id": "default"},
    )
    assert gone[0]["kind"] == "entity"
    assert g.get_node(ALICE) is None
    ep = g.execute(
        _helper("delete_episode_node"),
        {"uuid": EP, "group_id": "default"},
    )
    assert ep[0]["kind"] == "episode"
    assert g.get_node(EP) is None
