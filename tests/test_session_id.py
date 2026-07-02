from __future__ import annotations

from datetime import datetime, timezone

from mewcode.memory.models import SessionMemoryConfig
from mewcode.session_id import is_valid_session_id, new_session_id


def test_new_session_id_uses_timestamp_and_suffix() -> None:
    session_id = str(new_session_id(datetime(2026, 6, 12, 8, 9, 10, tzinfo=timezone.utc)))

    assert session_id.startswith("20260612-080910-")
    assert is_valid_session_id(session_id)


def test_new_session_id_avoids_same_second_collision() -> None:
    now = datetime(2026, 6, 12, 8, 9, 10, tzinfo=timezone.utc)
    ids = {new_session_id(now) for _ in range(50)}

    assert len(ids) == 50


def test_rejects_invalid_session_id() -> None:
    assert not is_valid_session_id("20260612-080910")
    assert not is_valid_session_id("bad")
    assert not is_valid_session_id("20260612-080910-zzzz")


def test_memory_config_defaults() -> None:
    config = SessionMemoryConfig()

    assert config.retention_days == 30
    assert config.index_max_lines == 200
    assert config.index_max_bytes == 25_000
