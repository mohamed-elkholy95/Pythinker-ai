"""Relaxed consolidation boundaries prevent fallthrough to lossy snip."""
from __future__ import annotations

from unittest.mock import MagicMock

from pythinker.agent.memory.consolidator import Consolidator
from pythinker.session.manager import Session


def _consolidator() -> Consolidator:
    return Consolidator(
        store=MagicMock(),
        provider=MagicMock(generation=MagicMock(max_tokens=8_192)),
        model="gpt-5.5",
        sessions=MagicMock(),
        context_window_tokens=65_536,
        build_messages=lambda **_: [],
        get_tool_definitions=lambda: [],
    )


def test_relaxed_finds_assistant_boundary_when_no_user_turn():
    session = Session(
        key="cli:test",
        messages=[
            {
                "role": "assistant",
                "content": "a1",
                "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "name": "exec", "content": "out"},
            {"role": "assistant", "content": "a2"},
        ],
        last_consolidated=0,
    )
    c = _consolidator()
    cut = c.pick_consolidation_boundary_relaxed(session, tokens_to_remove=1)
    assert cut is not None
    assert cut[0] == 2


def test_relaxed_returns_full_tail_when_no_legal_cut():
    session = Session(
        key="cli:test",
        messages=[
            {
                "role": "assistant",
                "content": "a1",
                "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "name": "exec", "content": "out"},
        ],
        last_consolidated=0,
    )
    c = _consolidator()
    cut = c.pick_consolidation_boundary_relaxed(session, tokens_to_remove=1)
    assert cut is not None
    assert cut[0] == len(session.messages)
