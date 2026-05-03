"""Tests for ``estimate_session_usage``: empty session, token counting,
and the overflow clamp + silent-failure warning."""
from pythinker.agent.usage import estimate_session_usage
from pythinker.config.schema import AgentDefaults
from pythinker.session.manager import Session


def test_empty_session_reports_zero_used():
    s = Session(key="websocket:abc")
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)
    result = estimate_session_usage(s, defaults)
    assert result["used"] == 0
    assert result["limit"] == 128_000


def test_messages_increase_used_count():
    s = Session(key="websocket:abc")
    s.add_message("user", "hello there, how are you doing today")
    s.add_message("assistant", "I'm well, thanks for asking. What can I help you with?")
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)
    result = estimate_session_usage(s, defaults)
    assert 0 < result["used"] < 200, "two short messages should be ~10–80 tokens"
    assert result["limit"] == 128_000


def test_used_never_exceeds_limit():
    """The pill renders a percentage; clamp used to limit so the bar never overflows."""
    s = Session(key="websocket:abc")
    long = "word " * 10_000
    s.add_message("user", long)
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=1_000)
    result = estimate_session_usage(s, defaults)
    assert result["used"] <= result["limit"]


def test_logs_warning_when_estimator_returns_zero_for_non_empty_session(monkeypatch, caplog):
    """If tiktoken silently fails (returns 0) on a session that actually has
    content, log a warning so operators can spot a broken install."""
    import logging
    s = Session(key="websocket:abc")
    s.add_message("user", "this is a real message")
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)

    monkeypatch.setattr(
        "pythinker.agent.usage.estimate_prompt_tokens",
        lambda _msgs, _tools: 0,
    )

    # Use loguru's propagate-to-stdlib hook for caplog capture.
    from loguru import logger as _loguru
    handler_id = _loguru.add(caplog.handler, format="{message}")
    try:
        with caplog.at_level(logging.WARNING):
            result = estimate_session_usage(s, defaults)
    finally:
        _loguru.remove(handler_id)

    assert result["used"] == 0
    assert any("tiktoken" in m.lower() for m in caplog.messages), \
        f"expected tiktoken-related warning, got {caplog.messages}"
