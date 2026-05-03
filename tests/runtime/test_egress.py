"""Tests for ToolEgressGateway: it gates ToolRegistry.execute via PolicyService."""

import json
from pathlib import Path

from pythinker.agent.tools.base import Tool, tool_parameters
from pythinker.agent.tools.registry import ToolRegistry
from pythinker.runtime.context import RequestContext
from pythinker.runtime.egress import ToolEgressGateway
from pythinker.runtime.policy import PolicyService
from pythinker.runtime.telemetry import JSONLSink, set_sink


@tool_parameters({"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input."

    async def execute(self, **kwargs):  # type: ignore[override]
        return f"echo:{kwargs['x']}"


@tool_parameters({"type": "object", "properties": {"action": {"type": "string"}}})
class _BrowserTool(Tool):
    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return "browser"

    async def execute(self, **kwargs):  # type: ignore[override]
        return f"browser:{kwargs['action']}"


def _ctx(**kw) -> RequestContext:
    return RequestContext.for_inbound(
        channel="cli", sender_id="u", chat_id="c", session_key="cli:c", **kw,
    )


async def test_egress_invokes_tool_when_policy_allows():
    reg = ToolRegistry()
    reg.register(_EchoTool())
    pol = PolicyService(enabled=True, allowed_tools={"default": ["echo"]})
    gw = ToolEgressGateway(registry=reg, policy=pol)
    result = await gw.execute(_ctx(), "echo", {"x": "hi"})
    assert result == "echo:hi"


async def test_egress_blocks_disallowed_tool_without_invoking(tmp_path: Path):
    """Denied calls must (a) skip invocation, AND (b) emit BOTH a tool_call
    (allowed=False) and a terminal tool_result (error=True) so audit
    consumers see a complete record for every attempt."""
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        reg = ToolRegistry()
        invoked = []

        @tool_parameters({"type": "object", "properties": {}})
        class _Spy(Tool):
            @property
            def name(self) -> str:
                return "spy"

            @property
            def description(self) -> str:
                return "spy"

            async def execute(self, **kwargs):
                invoked.append(True)
                return "ran"

        reg.register(_Spy())
        pol = PolicyService(enabled=True, allowed_tools={"default": ["something_else"]})
        gw = ToolEgressGateway(registry=reg, policy=pol)
        result = await gw.execute(_ctx(), "spy", {})
        assert "Policy denied" in result
        assert invoked == []  # tool was never called
    finally:
        set_sink(None)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    call_events = [r for r in rows if r["name"] == "tool_call"]
    result_events = [r for r in rows if r["name"] == "tool_result"]
    assert len(call_events) == 1 and call_events[0]["attributes"]["allowed"] is False
    # Terminal tool_result is the audit-completeness contract.
    assert len(result_events) == 1
    assert result_events[0]["attributes"]["error"] is True
    assert "reason" in result_events[0]["attributes"]


async def test_egress_denial_returned_string_starts_with_error():
    """Denials must use the 'Error:' prefix the runner detects.

    Regression: returning 'Policy denied: ...' without an 'Error:' prefix
    let AgentRunner._run_tool record the call as status='ok', so
    fail_on_tool_error never tripped and policy rejections were treated
    as successful work — silently weakening enforcement.
    """
    reg = ToolRegistry()
    pol = PolicyService(enabled=True, allowed_tools={"default": ["other"]})
    gw = ToolEgressGateway(registry=reg, policy=pol)
    result = await gw.execute(_ctx(), "anything", {})
    assert isinstance(result, str)
    assert result.startswith("Error")
    assert "Policy denied" in result


async def test_egress_emits_telemetry_for_call_and_result(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        reg = ToolRegistry()
        reg.register(_EchoTool())
        pol = PolicyService(enabled=True, allowed_tools={"default": ["echo"]})
        gw = ToolEgressGateway(registry=reg, policy=pol)
        await gw.execute(_ctx(), "echo", {"x": "hi"})
    finally:
        set_sink(None)
    names = [json.loads(line)["name"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert "tool_call" in names
    assert "tool_result" in names


async def test_egress_pass_through_when_policy_disabled():
    reg = ToolRegistry()
    reg.register(_EchoTool())
    gw = ToolEgressGateway(registry=reg, policy=PolicyService(enabled=False))
    assert await gw.execute(_ctx(), "echo", {"x": "ok"}) == "echo:ok"


async def test_egress_records_failures_as_tool_result_with_error_attribute(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        reg = ToolRegistry()

        @tool_parameters({"type": "object", "properties": {}})
        class _Boom(Tool):
            @property
            def name(self) -> str:
                return "boom"

            @property
            def description(self) -> str:
                return "always fails"

            async def execute(self, **kwargs):
                raise RuntimeError("kaboom")

        reg.register(_Boom())
        pol = PolicyService(enabled=True, allowed_tools={"default": ["boom"]})
        gw = ToolEgressGateway(registry=reg, policy=pol)
        result = await gw.execute(_ctx(), "boom", {})
        assert "Error executing boom" in result
    finally:
        set_sink(None)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    result_row = next(r for r in rows if r["name"] == "tool_result")
    assert result_row["attributes"]["error"] is True


async def test_egress_authorizes_specific_browser_action(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        reg = ToolRegistry()
        reg.register(_BrowserTool())
        pol = PolicyService(enabled=True, allowed_tools={"default": ["browser.navigate"]})
        gw = ToolEgressGateway(registry=reg, policy=pol)
        result = await gw.execute(_ctx(), "browser", {"action": "navigate"})
        assert result == "browser:navigate"
    finally:
        set_sink(None)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    call_row = next(r for r in rows if r["name"] == "tool_call")
    assert call_row["attributes"]["tool"] == "browser.navigate"


async def test_egress_falls_back_to_legacy_browser_allowlist(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        reg = ToolRegistry()
        reg.register(_BrowserTool())
        pol = PolicyService(enabled=True, allowed_tools={"default": ["browser"]})
        gw = ToolEgressGateway(registry=reg, policy=pol)
        result = await gw.execute(_ctx(), "browser", {"action": "navigate"})
        assert result == "browser:navigate"
    finally:
        set_sink(None)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    policy_rows = [r for r in rows if r["name"] == "policy_decision"]
    call_row = next(r for r in rows if r["name"] == "tool_call")
    assert [r["attributes"]["tool"] for r in policy_rows] == ["browser"]
    assert call_row["attributes"]["tool"] == "browser"
