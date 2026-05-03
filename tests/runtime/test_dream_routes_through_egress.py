"""Dream's tool calls flow through ToolEgressGateway under the system_dream policy entry."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pythinker.agent.memory import Dream, MemoryStore
from pythinker.agent.runner import AgentRunResult
from pythinker.agent.tools.base import Tool, tool_parameters
from pythinker.agent.tools.registry import ToolRegistry
from pythinker.runtime.context import RequestContext
from pythinker.runtime.egress import ToolEgressGateway
from pythinker.runtime.policy import PolicyService
from pythinker.runtime.telemetry import JSONLSink, set_sink


@tool_parameters({"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
class _ReadStub(Tool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "stub"

    async def execute(self, **kwargs):
        return f"read:{kwargs['path']}"


def _ctx(agent_id: str = "system_dream") -> RequestContext:
    return RequestContext(
        trace_id="t", span_id="s", parent_span_id=None,
        session_key="cron:dream", channel="cron",
        sender_id="system", chat_id="dream",
        agent_id=agent_id,
    )


def test_system_dream_agent_can_call_read_file_via_egress():
    """system_dream must reach read_file through the gateway, not around it."""
    pol = PolicyService(enabled=True)  # no manifests — deny-default for everyone else
    decision = pol.authorize_tool_call(_ctx(), "read_file")
    assert decision.allowed is True


def test_system_dream_cannot_call_unlisted_tool():
    """Exemption is narrow: system_dream must still be denied tools outside its list."""
    pol = PolicyService(enabled=True)
    decision = pol.authorize_tool_call(_ctx(), "exec")
    assert decision.allowed is False


def test_system_dream_exemption_can_be_overridden_to_empty():
    """Operators can disable the builtin exemption by passing builtin_exemptions={}."""
    pol = PolicyService(enabled=True, builtin_exemptions={})
    decision = pol.authorize_tool_call(_ctx(), "read_file")
    assert decision.allowed is False


def test_explicit_manifest_for_system_dream_wins_over_builtin_exemption():
    """If an operator declares system_dream in allowed_tools, the manifest entry wins."""
    pol = PolicyService(
        enabled=True,
        allowed_tools={"system_dream": ["read_file"]},  # narrower than builtin
    )
    assert pol.authorize_tool_call(_ctx(), "read_file").allowed is True
    assert pol.authorize_tool_call(_ctx(), "edit_file").allowed is False


async def test_dream_emits_tool_call_telemetry_via_egress(tmp_path: Path):
    """A Dream-style egress.execute call produces normal tool_call/tool_result events."""
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        reg = ToolRegistry()
        reg.register(_ReadStub())
        pol = PolicyService(enabled=True)  # exemptions kick in for system_dream
        gw = ToolEgressGateway(registry=reg, policy=pol)
        result = await gw.execute(_ctx(), "read_file", {"path": "MEMORY.md"})
        assert result == "read:MEMORY.md"
    finally:
        set_sink(None)
    names = [json.loads(line)["name"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert "tool_call" in names
    assert "tool_result" in names


async def test_dream_run_forwards_request_context_and_egress_to_runner(tmp_path: Path):
    """Dream.run forwards request_context + egress into AgentRunSpec.

    The runner's egress dispatch is covered by Task 7/9 — this asserts the
    handoff boundary: when Dream is given a context + egress, both reach
    AgentRunSpec unchanged so the runner can route through the gateway.
    """
    store = MemoryStore(tmp_path)
    store.append_history("user said something dream-worthy")

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="phase1 analysis"))

    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done",
        messages=[],
        stop_reason="completed",
        tool_events=[],
    ))

    dream = Dream(store=store, provider=provider, model="m", max_batch_size=5)
    dream._runner = mock_runner

    sentinel_egress = MagicMock(spec=ToolEgressGateway)
    ctx = _ctx()

    result = await dream.run(request_context=ctx, egress=sentinel_egress)

    assert result is True
    mock_runner.run.assert_called_once()
    spec = mock_runner.run.call_args[0][0]
    assert spec.request_context is ctx
    assert spec.request_context.agent_id == "system_dream"
    assert spec.egress is sentinel_egress


async def test_dream_run_without_context_passes_neither(tmp_path: Path):
    """Legacy callers (no kwargs) must still see request_context=None and egress=None.

    The XOR guard in AgentRunSpec.__post_init__ would raise if egress leaked
    through without a context. This test pins the legacy contract.
    """
    store = MemoryStore(tmp_path)
    store.append_history("legacy entry")

    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="phase1"))

    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done", messages=[], stop_reason="completed", tool_events=[],
    ))

    dream = Dream(store=store, provider=provider, model="m", max_batch_size=5)
    dream._runner = mock_runner

    await dream.run()

    spec = mock_runner.run.call_args[0][0]
    assert spec.request_context is None
    assert spec.egress is None
