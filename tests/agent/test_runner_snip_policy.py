"""Snip uses BudgetPolicy.hard as the trigger threshold."""
from __future__ import annotations

from unittest.mock import MagicMock

from pythinker.agent.budget import BudgetPolicy
from pythinker.agent.runner import AgentRunner, AgentRunSpec
from pythinker.agent.tools.registry import ToolRegistry


def _spec(window: int, max_tokens: int) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[],
        tools=ToolRegistry(),
        model="gpt-5.5",
        max_iterations=1,
        max_tool_result_chars=16_000,
        max_tokens=max_tokens,
        context_window_tokens=window,
        encoding="o200k_base",
    )


def test_snip_no_op_when_under_hard_budget(monkeypatch):
    """A prompt comfortably under policy.hard must not be snipped."""
    spec = _spec(window=272_000, max_tokens=24_000)
    policy = BudgetPolicy.for_model(window=272_000, output_reserve=24_000)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    runner = AgentRunner(MagicMock(generation=MagicMock(max_tokens=24_000)))
    monkeypatch.setattr(
        "pythinker.agent.runner.estimate_prompt_tokens_chain",
        lambda *a, **kw: (policy.hard - 1000, "tiktoken:o200k_base"),
    )
    out = runner._snip_history(spec, messages)
    assert out == messages


def test_snip_triggers_above_hard_budget(monkeypatch):
    """A prompt over policy.hard must be snipped even if legacy math allowed it."""
    spec = _spec(window=272_000, max_tokens=24_000)
    policy = BudgetPolicy.for_model(window=272_000, output_reserve=24_000)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    runner = AgentRunner(MagicMock(generation=MagicMock(max_tokens=24_000)))
    monkeypatch.setattr(
        "pythinker.agent.runner.estimate_prompt_tokens_chain",
        lambda *a, **kw: (policy.hard + 1000, "tiktoken:o200k_base"),
    )
    monkeypatch.setattr(
        "pythinker.agent.runner.estimate_message_tokens",
        lambda _msg: 100_000,
    )
    out = runner._snip_history(spec, messages)
    assert out != messages
    assert out[-2:] == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
