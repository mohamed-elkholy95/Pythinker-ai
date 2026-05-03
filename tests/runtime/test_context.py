"""Tests for RequestContext propagation and budget debiting."""

from pythinker.runtime.context import BudgetCounters, RequestContext


def test_for_inbound_generates_unique_trace_ids():
    a = RequestContext.for_inbound(channel="cli", sender_id="u", chat_id="c", session_key="cli:c")
    b = RequestContext.for_inbound(channel="cli", sender_id="u", chat_id="c", session_key="cli:c")
    assert a.trace_id != b.trace_id
    assert len(a.trace_id) == 32  # UUID4 hex


def test_for_inbound_carries_identity():
    ctx = RequestContext.for_inbound(
        channel="slack", sender_id="U123", chat_id="D456", session_key="slack:D456",
    )
    assert ctx.channel == "slack"
    assert ctx.sender_id == "U123"
    assert ctx.chat_id == "D456"
    assert ctx.session_key == "slack:D456"
    assert ctx.agent_id == "default"
    assert ctx.policy_version == 0
    assert ctx.recursion_depth == 0


def test_with_agent_id_returns_new_context_with_agent():
    ctx = RequestContext.for_inbound(channel="cli", sender_id="u", chat_id="c", session_key="cli:c")
    bound = ctx.with_agent_id("research-agent", policy_version=3)
    assert bound.agent_id == "research-agent"
    assert bound.policy_version == 3
    assert bound.trace_id == ctx.trace_id  # trace propagates
    assert ctx.agent_id == "default"  # original untouched


def test_child_for_subagent_inherits_trace_increments_depth():
    parent = RequestContext.for_inbound(
        channel="cli", sender_id="u", chat_id="c", session_key="cli:c",
    )
    child = parent.child_for_subagent(label="research")
    assert child.trace_id == parent.trace_id  # same trace, different span
    assert child.span_id != parent.span_id
    assert child.parent_span_id == parent.span_id
    assert child.recursion_depth == parent.recursion_depth + 1


def test_budget_counters_debit_returns_remaining():
    bc = BudgetCounters(max_tool_calls=3, max_wall_clock_s=60.0)
    assert bc.debit_tool_call() == 2
    assert bc.debit_tool_call() == 1
    assert bc.debit_tool_call() == 0
    assert bc.debit_tool_call() == -1  # exhausted; caller checks the sign


def test_budget_counters_zero_means_disabled():
    bc = BudgetCounters(max_tool_calls=0, max_wall_clock_s=0.0)
    assert bc.debit_tool_call() == 0  # disabled budget always reports 0 remaining (never blocks)
    assert bc.is_disabled()
