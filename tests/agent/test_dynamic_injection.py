"""Phase 5 of the coding-prompt uplift — dynamic injections.

Covers:
  * The ``apply_injections`` helper places content correctly per
    placement (append / prepend).
  * ``CompositeInjectionProvider`` swallows provider exceptions so a
    bad provider can't break a turn.
  * ``PlanModeProvider`` is a no-op without a plan file; emits the full
    plan on the first turn; emits the sparse reminder on cadence.
  * ``AfkModeProvider`` only fires on the synthetic-origin channels
    (heartbeat / cron / scheduled) and only on iteration 0.
  * ``AgentRunSpec.dynamic_injection_provider`` defaults to None — no
    behavior change for legacy callers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pythinker.agent.dynamic_injection import (
    CompositeInjectionProvider,
    DynamicInjection,
    DynamicInjectionProvider,
    apply_injections,
)


class _Static(DynamicInjectionProvider):
    """Test fixture — always emits the configured injections."""

    def __init__(self, items: list[DynamicInjection]) -> None:
        self._items = list(items)

    def get_injections(self, messages, *, iteration, session_key=None):
        return list(self._items)


class _Boom(DynamicInjectionProvider):
    """Test fixture — always raises."""

    def get_injections(self, messages, *, iteration, session_key=None):
        raise RuntimeError("nope")


def test_apply_injections_no_op_when_empty():
    msgs = [{"role": "user", "content": "hi"}]
    assert apply_injections(msgs, []) == msgs


def test_apply_injections_appends_at_end():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    out = apply_injections(
        msgs,
        [DynamicInjection(content="reminder", role="system", placement="append")],
    )
    assert out[-1] == {"role": "system", "content": "reminder"}
    assert len(out) == 3


def test_apply_injections_prepends_before_trailing_user():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    out = apply_injections(
        msgs,
        [DynamicInjection(content="seed", role="system", placement="prepend")],
    )
    # prepended before the trailing user message
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "seed"},
        {"role": "user", "content": "hi"},
    ]


def test_apply_injections_prepends_at_front_when_no_user_message():
    msgs = [{"role": "system", "content": "sys"}]
    out = apply_injections(
        msgs,
        [DynamicInjection(content="seed", role="system", placement="prepend")],
    )
    assert out[0] == {"role": "system", "content": "seed"}


def test_composite_swallows_provider_exceptions():
    good = _Static([DynamicInjection(content="ok")])
    bad = _Boom()
    composite = CompositeInjectionProvider([bad, good])
    out = composite.get_injections([], iteration=0, session_key="cli:c")
    assert len(out) == 1
    assert out[0].content == "ok"


def test_agent_run_spec_default_dynamic_injection_provider_is_none():
    """Legacy callers that don't set the new field must keep working unchanged."""
    from pythinker.agent.runner import AgentRunSpec
    from pythinker.agent.tools.registry import ToolRegistry

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=ToolRegistry(),
        model="m",
        max_iterations=1,
        max_tool_result_chars=4096,
    )
    assert spec.dynamic_injection_provider is None


# ---------------------------------------------------------------------------
# PlanModeProvider
# ---------------------------------------------------------------------------


def _make_plan_workspace(tmp_path: Path, body: str = "1. step one\n2. step two\n") -> Path:
    plan_dir = tmp_path / ".pythinker"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_plan_mode_no_op_without_plan_file(tmp_path):
    from pythinker.agent.dynamic_injections import PlanModeProvider

    provider = PlanModeProvider(workspace=tmp_path)
    assert provider.get_injections([], iteration=0, session_key="cli:c") == []


def test_plan_mode_emits_full_on_first_turn(tmp_path):
    from pythinker.agent.dynamic_injections import PlanModeProvider

    ws = _make_plan_workspace(tmp_path, body="numbered plan")
    provider = PlanModeProvider(workspace=ws)
    out = provider.get_injections([], iteration=0, session_key="cli:c")
    assert len(out) == 1
    assert "Active plan" in out[0].content
    assert "numbered plan" in out[0].content
    assert out[0].metadata["kind"] == "full"


def test_plan_mode_sparse_cadence(tmp_path):
    from pythinker.agent.dynamic_injections import PlanModeProvider

    ws = _make_plan_workspace(tmp_path)
    provider = PlanModeProvider(workspace=ws, sparse_every=3)

    # Turn 1: full
    full = provider.get_injections([], iteration=0, session_key="cli:c")
    assert full[0].metadata["kind"] == "full"

    # Turns 2 + 3: nothing
    for _ in range(2):
        assert provider.get_injections([], iteration=0, session_key="cli:c") == []

    # Turn 4 (counter == sparse_every): sparse reminder
    sparse = provider.get_injections([], iteration=0, session_key="cli:c")
    assert len(sparse) == 1
    assert sparse[0].metadata["kind"] == "sparse"


def test_plan_mode_truncates_oversize_plan(tmp_path):
    from pythinker.agent.dynamic_injections import PlanModeProvider

    ws = _make_plan_workspace(tmp_path, body="x" * 10_000)
    provider = PlanModeProvider(workspace=ws, max_full_chars=500)
    out = provider.get_injections([], iteration=0, session_key="cli:c")
    assert "[plan truncated" in out[0].content


def test_plan_mode_per_session_counters(tmp_path):
    """Counter is keyed per session_key so two channels don't share state."""
    from pythinker.agent.dynamic_injections import PlanModeProvider

    ws = _make_plan_workspace(tmp_path)
    provider = PlanModeProvider(workspace=ws, sparse_every=10)

    a = provider.get_injections([], iteration=0, session_key="cli:a")
    b = provider.get_injections([], iteration=0, session_key="cli:b")
    assert a[0].metadata["kind"] == "full"
    assert b[0].metadata["kind"] == "full"  # both get a fresh "full" reminder


# ---------------------------------------------------------------------------
# AfkModeProvider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", ["heartbeat", "cron", "scheduled"])
def test_afk_mode_emits_on_synthetic_channels(channel):
    from pythinker.agent.dynamic_injections import AfkModeProvider

    provider = AfkModeProvider()
    out = provider.get_injections([], iteration=0, session_key=f"{channel}:job1")
    assert len(out) == 1
    assert "AFK mode" in out[0].content


@pytest.mark.parametrize("channel", ["cli", "telegram", "slack", "email"])
def test_afk_mode_silent_on_human_channels(channel):
    from pythinker.agent.dynamic_injections import AfkModeProvider

    provider = AfkModeProvider()
    assert provider.get_injections([], iteration=0, session_key=f"{channel}:c") == []


def test_afk_mode_only_iteration_zero():
    from pythinker.agent.dynamic_injections import AfkModeProvider

    provider = AfkModeProvider()
    assert provider.get_injections([], iteration=0, session_key="cron:job") != []
    assert provider.get_injections([], iteration=1, session_key="cron:job") == []


def test_afk_mode_silent_without_session_key():
    from pythinker.agent.dynamic_injections import AfkModeProvider

    provider = AfkModeProvider()
    assert provider.get_injections([], iteration=0, session_key=None) == []
