"""estimate_session_usage exposes the floor for stacked usage bars."""
from __future__ import annotations

from pythinker.agent.usage import estimate_session_usage
from pythinker.config.schema import AgentDefaults
from pythinker.session.manager import Session


def test_usage_includes_floor_when_floor_callback_provided():
    s = Session(key="websocket:abc")
    s.add_message("user", "hello")
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)
    usage = estimate_session_usage(
        s,
        defaults,
        floor_tokens=2_500,
    )
    assert usage["used"] > 0
    assert usage["limit"] == 128_000
    assert usage["floor"] == 2_500


def test_usage_omits_floor_when_not_provided():
    s = Session(key="websocket:abc")
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)
    usage = estimate_session_usage(s, defaults)
    assert "floor" not in usage or usage["floor"] == 0
