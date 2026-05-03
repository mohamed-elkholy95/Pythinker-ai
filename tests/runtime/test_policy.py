"""Tests for PolicyService.authorize_ingress / authorize_tool_call."""

from pythinker.runtime.context import BudgetCounters, RequestContext
from pythinker.runtime.policy import PolicyService


def _ctx(*, budgets: BudgetCounters | None = None) -> RequestContext:
    return RequestContext.for_inbound(
        channel="cli", sender_id="u", chat_id="c",
        session_key="cli:c", budgets=budgets,
    )


def test_disabled_policy_allows_everything():
    svc = PolicyService(enabled=False)
    assert svc.authorize_ingress(_ctx()).allowed is True
    assert svc.authorize_tool_call(_ctx(), "anything").allowed is True


def test_enabled_policy_with_no_allowlist_denies_by_default():
    """policy_enabled=True without an allow-list is a misconfig — deny everything."""
    svc = PolicyService(enabled=True, allowed_tools={})
    decision = svc.authorize_tool_call(_ctx(), "read_file")
    assert decision.allowed is False
    assert "no allow-list configured" in decision.reason


def test_enabled_policy_default_denies_unknown_tool():
    svc = PolicyService(enabled=True, allowed_tools={"default": ["read_file"]})
    decision = svc.authorize_tool_call(_ctx(), "exec")
    assert decision.allowed is False
    assert "not in allow-list" in decision.reason


def test_enabled_policy_allows_listed_tool():
    svc = PolicyService(enabled=True, allowed_tools={"default": ["read_file", "grep"]})
    assert svc.authorize_tool_call(_ctx(), "read_file").allowed is True
    assert svc.authorize_tool_call(_ctx(), "grep").allowed is True


def test_allowlist_inspection_checks_names_without_debiting_budget():
    svc = PolicyService(enabled=True, allowed_tools={"default": ["browser.navigate"]})
    ctx = _ctx(budgets=BudgetCounters(max_tool_calls=1, max_wall_clock_s=0.0))
    assert svc.tool_name_allowed_by_allowlist(ctx, "browser.navigate") is True
    assert svc.tool_name_allowed_by_allowlist(ctx, "browser.click") is False
    assert ctx.budgets.tool_calls_used == 0


def test_migration_mode_allows_all_tools():
    """policy_enabled=True with migration_mode="allow-all" is the opt-in escape hatch."""
    svc = PolicyService(enabled=True, allowed_tools={}, migration_mode="allow-all")
    assert svc.authorize_tool_call(_ctx(), "exec").allowed is True
    assert svc.authorize_tool_call(_ctx(), "anything").allowed is True


def test_migration_mode_still_enforces_budgets():
    """Migration mode bypasses the allow-list ONLY — budgets still bite."""
    svc = PolicyService(enabled=True, allowed_tools={}, migration_mode="allow-all")
    ctx = _ctx(budgets=BudgetCounters(max_tool_calls=2, max_wall_clock_s=0.0))
    assert svc.authorize_tool_call(ctx, "exec").allowed is True   # 1/2
    assert svc.authorize_tool_call(ctx, "exec").allowed is True   # 2/2
    decision = svc.authorize_tool_call(ctx, "exec")               # 3rd call exceeds
    assert decision.allowed is False
    assert "max_tool_calls" in decision.reason


def test_empty_explicit_allowlist_denies_without_falling_back():
    """A manifest that sets allowed_tools=[] explicitly denies that agent.

    Empty list must NOT fall back to the "default" agent's allow-list — that
    would make 'lock this agent down' indistinguishable from 'no manifest'.
    """
    svc = PolicyService(
        enabled=True,
        allowed_tools={
            "default": ["read_file", "exec"],
            "locked": [],  # explicit deny-everything for "locked"
        },
    )
    locked_ctx = _ctx().with_agent_id("locked")
    decision = svc.authorize_tool_call(locked_ctx, "read_file")
    assert decision.allowed is False
    assert "not in allow-list for agent 'locked'" in decision.reason


def test_explicit_wildcard_allows_all_tools_for_agent():
    """Operators can also opt in per-agent by setting allowed_tools={"agent": ["*"]}."""
    svc = PolicyService(enabled=True, allowed_tools={"default": ["*"]})
    assert svc.authorize_tool_call(_ctx(), "exec").allowed is True


def test_per_agent_overrides_default():
    svc = PolicyService(
        enabled=True,
        allowed_tools={"default": ["read_file"], "research": ["*"]},
    )
    base = _ctx().with_agent_id("research")
    assert svc.authorize_tool_call(base, "exec").allowed is True
    other = _ctx().with_agent_id("default")
    assert svc.authorize_tool_call(other, "exec").allowed is False


def test_budget_exhaustion_denies_tool_call():
    svc = PolicyService(enabled=True, allowed_tools={"default": ["*"]})
    ctx = _ctx(budgets=BudgetCounters(max_tool_calls=2, max_wall_clock_s=0.0))
    assert svc.authorize_tool_call(ctx, "read_file").allowed is True  # 1/2
    assert svc.authorize_tool_call(ctx, "read_file").allowed is True  # 2/2
    decision = svc.authorize_tool_call(ctx, "read_file")  # 3rd call exceeds
    assert decision.allowed is False
    assert "max_tool_calls" in decision.reason


def test_recursion_depth_limit_denies():
    svc = PolicyService(enabled=True, allowed_tools={"default": ["*"]}, max_recursion_depth=2)
    parent = _ctx()
    child1 = parent.child_for_subagent(label="a")
    child2 = child1.child_for_subagent(label="b")
    child3 = child2.child_for_subagent(label="c")
    assert svc.authorize_tool_call(child1, "read_file").allowed is True  # depth 1
    assert svc.authorize_tool_call(child2, "read_file").allowed is True  # depth 2
    decision = svc.authorize_tool_call(child3, "read_file")  # depth 3 > limit
    assert decision.allowed is False
    assert "recursion" in decision.reason


def test_authorize_ingress_can_block_sender():
    svc = PolicyService(enabled=True, allowed_tools={"default": ["*"]}, blocked_senders={"slack:U999"})
    ctx = RequestContext.for_inbound(
        channel="slack", sender_id="U999", chat_id="C1", session_key="slack:C1",
    )
    decision = svc.authorize_ingress(ctx)
    assert decision.allowed is False
    # Reason must be non-identifying: it flows into telemetry, logs, and
    # PermissionError messages that may surface to API clients. The raw
    # sender id (e.g. "slack:U999") must NOT appear in decision.reason.
    assert decision.reason == "sender is blocked"
    assert "U999" not in decision.reason
    assert "slack" not in decision.reason
