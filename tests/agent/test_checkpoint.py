"""Focused tests for CheckpointManager.

Locks the literal session-metadata key strings (live-session migration risk
per AGENTS.md) and exercises set/clear/restore round-trips.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pythinker.agent.checkpoint import (
    PENDING_USER_TURN_KEY,
    RUNTIME_CHECKPOINT_KEY,
    CheckpointManager,
)
from pythinker.session.manager import Session


def test_metadata_key_literals_are_stable() -> None:
    """These literal values are persisted into sessions on disk. Changing them
    breaks every live session — AGENTS.md flags this as a migration boundary."""
    assert RUNTIME_CHECKPOINT_KEY == "runtime_checkpoint"
    assert PENDING_USER_TURN_KEY == "pending_user_turn"
    assert CheckpointManager.RUNTIME_CHECKPOINT_KEY == "runtime_checkpoint"
    assert CheckpointManager.PENDING_USER_TURN_KEY == "pending_user_turn"


def _make_manager() -> CheckpointManager:
    sessions = MagicMock()
    sessions.save = MagicMock()
    return CheckpointManager(sessions=sessions)


def test_set_then_clear_runtime_checkpoint_round_trip() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")

    cm.set_runtime_checkpoint(session, {"phase": "after_tool", "messages": [{"role": "tool"}]})
    assert session.metadata[RUNTIME_CHECKPOINT_KEY] == {
        "phase": "after_tool",
        "messages": [{"role": "tool"}],
    }
    cm.sessions.save.assert_called_once_with(session)

    cm.clear_runtime_checkpoint(session)
    assert RUNTIME_CHECKPOINT_KEY not in session.metadata


def test_mark_and_clear_pending_user_turn() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")

    cm.mark_pending_user_turn(session)
    assert session.metadata[PENDING_USER_TURN_KEY] is True

    cm.clear_pending_user_turn(session)
    assert PENDING_USER_TURN_KEY not in session.metadata


def test_restore_runtime_checkpoint_returns_false_when_absent() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")
    assert cm.restore_runtime_checkpoint(session) is False


def test_restore_runtime_checkpoint_appends_assistant_and_clears_keys() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")
    session.metadata[RUNTIME_CHECKPOINT_KEY] = {
        "assistant_message": {"role": "assistant", "content": "partial"},
        "completed_tool_results": [],
        "pending_tool_calls": [],
    }
    session.metadata[PENDING_USER_TURN_KEY] = True

    assert cm.restore_runtime_checkpoint(session) is True
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "partial"
    # restore clears both keys
    assert RUNTIME_CHECKPOINT_KEY not in session.metadata
    assert PENDING_USER_TURN_KEY not in session.metadata


def test_restore_runtime_checkpoint_synthesizes_interrupt_for_pending_tools() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")
    session.metadata[RUNTIME_CHECKPOINT_KEY] = {
        "assistant_message": {"role": "assistant", "content": "call tools"},
        "completed_tool_results": [],
        "pending_tool_calls": [
            {"id": "call_1", "function": {"name": "my_tool"}},
        ],
    }

    cm.restore_runtime_checkpoint(session)

    tool_msg = session.messages[-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["name"] == "my_tool"
    assert "interrupted" in tool_msg["content"].lower()


def test_restore_pending_user_turn_synthesizes_assistant_reply() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")
    session.metadata[PENDING_USER_TURN_KEY] = True
    session.messages.append({"role": "user", "content": "hi"})

    assert cm.restore_pending_user_turn(session) is True
    assert session.messages[-1]["role"] == "assistant"
    assert "interrupted" in session.messages[-1]["content"].lower()
    assert PENDING_USER_TURN_KEY not in session.metadata


def test_restore_pending_user_turn_returns_false_when_no_flag() -> None:
    cm = _make_manager()
    session = Session(key="cli:c")
    assert cm.restore_pending_user_turn(session) is False
