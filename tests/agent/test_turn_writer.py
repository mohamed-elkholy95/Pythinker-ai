"""Focused tests for TurnWriter — persistence + sanitization.

Covers:
- save_turn round-trip with text and tool content
- runtime-context-only user messages are dropped (no payload remains)
- oversized tool results are truncated
- image blocks become text placeholders with path
- persist_subagent_followup dedupes by subagent_task_id
"""

from __future__ import annotations

from pythinker.agent.context import ContextBuilder
from pythinker.agent.turn_writer import TurnWriter
from pythinker.bus.events import InboundMessage
from pythinker.session.manager import Session


def _make_writer(max_chars: int = 1000) -> TurnWriter:
    return TurnWriter(max_tool_result_chars=max_chars)


def test_save_turn_appends_assistant_with_text() -> None:
    writer = _make_writer()
    session = Session(key="cli:c")
    writer.save_turn(
        session,
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        skip=1,
    )
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "hello"
    assert "timestamp" in session.messages[-1]


def test_save_turn_skips_empty_assistant_messages() -> None:
    """Empty assistant messages with no tool_calls poison context — drop them."""
    writer = _make_writer()
    session = Session(key="cli:c")
    writer.save_turn(
        session,
        [{"role": "assistant", "content": "", "tool_calls": None}],
        skip=0,
    )
    assert session.messages == []


def test_save_turn_drops_runtime_context_only_user_message() -> None:
    writer = _make_writer()
    session = Session(key="cli:c")
    rt_only = (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\nCurrent Time: 2026\n"
        + ContextBuilder._RUNTIME_CONTEXT_END
    )
    writer.save_turn(session, [{"role": "user", "content": rt_only}], skip=0)
    assert session.messages == []


def test_save_turn_strips_runtime_context_prefix_keeps_user_text() -> None:
    writer = _make_writer()
    session = Session(key="cli:c")
    full = (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\nCurrent Time: 2026\n"
        + ContextBuilder._RUNTIME_CONTEXT_END
        + "\n\nthe real user message"
    )
    writer.save_turn(session, [{"role": "user", "content": full}], skip=0)
    assert len(session.messages) == 1
    assert session.messages[0]["content"] == "the real user message"


def test_save_turn_truncates_oversized_tool_results() -> None:
    writer = _make_writer(max_chars=20)
    session = Session(key="cli:c")
    huge = "x" * 200
    writer.save_turn(
        session,
        [{"role": "tool", "tool_call_id": "t1", "name": "exec", "content": huge}],
        skip=0,
    )
    assert len(session.messages[-1]["content"]) < 200


def test_sanitize_persisted_blocks_replaces_image_with_placeholder() -> None:
    writer = _make_writer()
    blocks = [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAA="},
            "_meta": {"path": "/tmp/cat.png"},
        }
    ]
    filtered = writer.sanitize_persisted_blocks(blocks)
    assert filtered[0]["type"] == "text"
    assert "cat.png" in filtered[0]["text"]


def test_persist_subagent_followup_appends_and_dedupes() -> None:
    writer = _make_writer()
    session = Session(key="cli:c")
    msg = InboundMessage(
        channel="cli",
        sender_id="sub-agent",
        chat_id="c",
        content="subagent result",
        metadata={"subagent_task_id": "task-1"},
    )

    assert writer.persist_subagent_followup(session, msg) is True
    assert session.messages[-1]["content"] == "subagent result"
    assert session.messages[-1]["subagent_task_id"] == "task-1"

    # Dedup: second call with same task_id is a no-op.
    assert writer.persist_subagent_followup(session, msg) is False
    assert len(session.messages) == 1


def test_persist_subagent_followup_skips_empty_content() -> None:
    writer = _make_writer()
    session = Session(key="cli:c")
    msg = InboundMessage(channel="cli", sender_id="s", chat_id="c", content="")
    assert writer.persist_subagent_followup(session, msg) is False
    assert session.messages == []
