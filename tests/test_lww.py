from graph_ted_db.store.lww import candidate_from_live, candidate_from_tombstone, winner
from graph_ted_db.store.records import NodeRecord, Tombstone

ID = "550e8400-e29b-41d4-a716-446655440000"


def _node(updated_at: str, name: str, v: int = 1) -> NodeRecord:
    return NodeRecord.from_dict(
        {
            "id": ID,
            "v": v,
            "updated_at": updated_at,
            "labels": ["Entity"],
            "props": {"name": name},
        }
    )


def test_newer_timestamp_wins():
    older = candidate_from_live(_node("2026-08-25T12:00:00Z", "old"))
    newer = candidate_from_live(_node("2026-08-25T13:00:00Z", "new"))
    assert winner(older, newer).payload.props["name"] == "new"  # type: ignore[union-attr]


def test_tombstone_beats_live_on_equal_timestamp():
    live = candidate_from_live(_node("2026-08-25T12:00:00Z", "live"))
    dead = candidate_from_tombstone(
        Tombstone.from_dict(
            {"id": ID, "kind": "node", "updated_at": "2026-08-25T12:00:00Z"}
        )
    )
    assert winner(live, dead).is_tombstone is True


def test_live_with_later_timestamp_resurrects():
    dead = candidate_from_tombstone(
        Tombstone.from_dict(
            {"id": ID, "kind": "node", "updated_at": "2026-08-25T12:00:00Z"}
        )
    )
    live = candidate_from_live(_node("2026-08-25T12:00:01Z", "back"))
    picked = winner(dead, live)
    assert picked.is_tombstone is False
    assert picked.payload.props["name"] == "back"  # type: ignore[union-attr]


def test_higher_v_breaks_remaining_tie():
    a = candidate_from_live(_node("2026-08-25T12:00:00Z", "a", v=1))
    b = candidate_from_live(_node("2026-08-25T12:00:00Z", "b", v=2))
    assert winner(a, b).payload.props["name"] == "b"  # type: ignore[union-attr]


def test_lww_picks_one_complete_record_not_a_field_merge():
    """Concurrent edits of different props: one whole snapshot wins, no splice."""
    alice = candidate_from_live(
        NodeRecord.from_dict(
            {
                "id": ID,
                "updated_at": "2026-08-25T12:00:00Z",
                "labels": ["Entity"],
                "props": {"name": "Alice", "summary": "from-alice"},
            }
        )
    )
    bob = candidate_from_live(
        NodeRecord.from_dict(
            {
                "id": ID,
                "updated_at": "2026-08-25T12:00:01Z",
                "labels": ["Entity"],
                "props": {"name": "Bob", "city": "from-bob"},
            }
        )
    )
    picked = winner(alice, bob)
    assert isinstance(picked.payload, NodeRecord)
    assert picked.payload.props == {"name": "Bob", "city": "from-bob"}
    assert "summary" not in picked.payload.props
