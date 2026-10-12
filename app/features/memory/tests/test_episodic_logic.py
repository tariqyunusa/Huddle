from datetime import datetime, timedelta
from types import SimpleNamespace

from app.features.memory.episodic import (
    build_memory_context,
    episode_is_before_live_window,
    should_create_episode,
)


def test_episode_threshold():
    assert not should_create_episode(19, 20)
    assert should_create_episode(20, 20)


def test_context_keeps_higher_ranked_episodes_and_fits_limit():
    date = datetime(2026, 1, 1)
    episodes = [
        SimpleNamespace(first_message_at=date, last_message_at=date, summary="high rank"),
        SimpleNamespace(first_message_at=date, last_message_at=date, summary="low rank"),
    ]
    limit = len("[Memory — earlier in this session]\n2026-01-01 to 2026-01-01: high rank")
    context = build_memory_context(episodes, max_chars=limit)
    assert "high rank" in context
    assert "low rank" not in context
    assert len(context) <= limit


def test_live_window_exclusion_boundary():
    boundary = datetime(2026, 1, 2)
    assert episode_is_before_live_window(boundary - timedelta(seconds=1), boundary)
    assert not episode_is_before_live_window(boundary, boundary)
