from datetime import datetime, timezone

import pytest

from graph_ted_db.store.format import (
    SHARD_FANOUT,
    format_timestamp,
    normalize_uuid,
    parse_timestamp,
    shard_id,
)


def test_shard_id_uses_first_uuid_byte():
    assert shard_id("550e8400-e29b-41d4-a716-446655440000") == "55"
    assert shard_id("00" + "0" * 30) == "00"
    assert shard_id("FF" + "0" * 30) == "ff"


def test_normalize_uuid_accepts_bare_hex():
    assert (
        normalize_uuid("550e8400e29b41d4a716446655440000")
        == "550e8400-e29b-41d4-a716-446655440000"
    )


def test_normalize_uuid_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_uuid("not-a-uuid")


def test_timestamp_roundtrip():
    instant = datetime(2026, 8, 25, 15, 4, 5, 123456, tzinfo=timezone.utc)
    text = format_timestamp(instant)
    assert text == "2026-08-25T15:04:05.123456Z"
    assert parse_timestamp(text) == instant
    assert parse_timestamp("2026-08-25T15:04:05Z") == datetime(
        2026, 8, 25, 15, 4, 5, tzinfo=timezone.utc
    )


def test_unsupported_fanout():
    with pytest.raises(ValueError):
        shard_id("550e8400-e29b-41d4-a716-446655440000", fanout=SHARD_FANOUT + 1)
