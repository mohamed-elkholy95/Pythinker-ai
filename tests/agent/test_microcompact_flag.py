"""Microcompact uses per-tool compactable flags instead of a hardcoded set."""
from __future__ import annotations

from unittest.mock import MagicMock

from pythinker.agent.runner import AgentRunner


def test_microcompact_uses_compactable_flag():
    runner = AgentRunner(MagicMock())
    runner._tool_is_compactable = lambda name: name in {"notion-search", "read_file"}

    messages = [
        {"role": "user", "content": "u"},
        *[
            {"role": "tool", "tool_call_id": f"t{i}", "name": "notion-search", "content": "x" * 600}
            for i in range(11)
        ],
        {"role": "assistant", "content": "a"},
    ]
    out = runner._microcompact(messages)
    compacted = [
        m for m in out
        if isinstance(m.get("content"), str) and "result omitted" in m["content"]
    ]
    assert len(compacted) >= 1


def test_microcompact_skips_non_compactable():
    runner = AgentRunner(MagicMock())
    runner._tool_is_compactable = lambda name: name == "read_file"

    messages = [
        {"role": "tool", "tool_call_id": f"t{i}", "name": "message", "content": "x" * 600}
        for i in range(11)
    ]
    out = runner._microcompact(messages)
    for m in out:
        if m.get("name") == "message":
            assert "result omitted" not in (m.get("content") or "")
