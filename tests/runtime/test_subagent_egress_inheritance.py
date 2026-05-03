"""Subagents inherit parent egress + child context, and recursion depth bites at spawn AND egress."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from pythinker.runtime.context import RequestContext


async def test_subagent_spawn_passes_child_context_to_runner(tmp_path):
    from pythinker.agent.subagent import SubagentManager
    from pythinker.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.subagent.AgentRunner") as mock_runner:
        mock_runner.return_value.run = AsyncMock()
        sm = SubagentManager(
            provider=provider, workspace=tmp_path, bus=bus,
            max_tool_result_chars=4096, model="m",
        )
        parent_ctx = RequestContext.for_inbound(
            channel="cli", sender_id="u", chat_id="c", session_key="cli:c",
        )
        # NOTE: parent_egress=object() is required because the AgentRunSpec
        # XOR guard rejects request_context-without-egress. The verbatim plan
        # test omitted parent_egress; we add a sentinel here so the spec
        # construction succeeds and the test can assert on the child context.
        await sm.spawn(
            task="do thing",
            label="research",
            origin_channel="cli",
            origin_chat_id="c",
            session_key="cli:c",
            parent_context=parent_ctx,
            parent_egress=object(),
        )
        # Drain background tasks so the patched runner.run has actually been
        # invoked before we assert on its call args. spawn() schedules a
        # task but doesn't yield, so without this gather the bg task never
        # runs while the patch is active.
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
        spec = mock_runner.return_value.run.call_args.args[0]
        child = spec.request_context
        assert child.trace_id == parent_ctx.trace_id
        assert child.recursion_depth == parent_ctx.recursion_depth + 1
        assert child.parent_span_id == parent_ctx.span_id


async def test_subagent_spawn_forwards_parent_egress(tmp_path):
    from pythinker.agent.subagent import SubagentManager
    from pythinker.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    sentinel_egress = object()  # any non-None value — runner only checks identity
    with patch("pythinker.agent.subagent.AgentRunner") as mock_runner:
        mock_runner.return_value.run = AsyncMock()
        sm = SubagentManager(
            provider=provider, workspace=tmp_path, bus=bus,
            max_tool_result_chars=4096, model="m",
        )
        parent_ctx = RequestContext.for_inbound(
            channel="cli", sender_id="u", chat_id="c", session_key="cli:c",
        )
        await sm.spawn(
            task="do thing",
            label="research",
            origin_channel="cli",
            origin_chat_id="c",
            session_key="cli:c",
            parent_context=parent_ctx,
            parent_egress=sentinel_egress,
        )
        # Drain background tasks before asserting on the mock's call args.
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
        spec = mock_runner.return_value.run.call_args.args[0]
        assert spec.egress is sentinel_egress


async def test_spawn_rejects_when_recursion_would_exceed_limit(tmp_path):
    """Spawn-time fast-fail: child's would-be depth > limit ⇒ no task is created."""
    from pythinker.agent.subagent import SubagentManager
    from pythinker.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.subagent.AgentRunner") as mock_runner:
        sm = SubagentManager(
            provider=provider, workspace=tmp_path, bus=bus,
            max_tool_result_chars=4096, model="m",
            max_recursion_depth=2,
        )
        deep_parent = RequestContext(
            trace_id="t", span_id="s", parent_span_id=None,
            session_key="cli:c", channel="cli", sender_id="u", chat_id="c",
            recursion_depth=2,  # already at the limit; child would be 3
        )
        result = await sm.spawn(
            task="x", label="x",
            origin_channel="cli", origin_chat_id="c",
            session_key="cli:c",
            parent_context=deep_parent,
        )
        # Spawn should refuse and the runner must not have been called.
        assert result is not None and "Spawn rejected" in result
        mock_runner.return_value.run.assert_not_called()


async def test_egress_denies_at_depth_when_spawn_check_was_skipped():
    """Defense-in-depth check: even if SubagentManager was constructed without
    a depth limit (e.g. test fixture, or a future bug), the egress gateway
    still blocks the over-deep tool call. Proves the second defense is
    actually reachable — not just dead code behind the spawn fast-fail."""
    from pythinker.agent.tools.base import Tool, tool_parameters
    from pythinker.agent.tools.registry import ToolRegistry
    from pythinker.runtime.egress import ToolEgressGateway
    from pythinker.runtime.policy import PolicyService

    @tool_parameters({"type": "object", "properties": {}})
    class _Spy(Tool):
        @property
        def name(self) -> str:
            return "spy"

        @property
        def description(self) -> str:
            return "spy"

        async def execute(self, **kwargs):
            return "ran"

    reg = ToolRegistry()
    reg.register(_Spy())
    pol = PolicyService(enabled=True, allowed_tools={"default": ["*"]}, max_recursion_depth=2)
    gw = ToolEgressGateway(registry=reg, policy=pol)

    # Construct an over-deep context directly — bypasses the spawn-time check.
    over_deep = RequestContext(
        trace_id="t", span_id="s", parent_span_id=None,
        session_key="cli:c", channel="cli", sender_id="u", chat_id="c",
        recursion_depth=3,  # one past the limit
    )
    result = await gw.execute(over_deep, "spy", {})
    assert "Policy denied" in result
    assert "recursion depth" in result


async def test_spawn_tool_context_does_not_leak_across_concurrent_sessions(tmp_path):
    """ContextVar isolation: setting parent_context for session A must not
    bleed into a concurrently-running session B's spawn call.

    Pattern (Python 3.11+): pass an explicit `context=` to `asyncio.create_task`
    so each Task runs in its own ContextVar scope. Per the asyncio docs:
    "If *context* is not provided, the current context copy is created."
    Passing it explicitly here makes the per-task isolation a property of the
    test, not of the parent context's pre-set state.
    """
    import asyncio
    import contextvars
    from unittest.mock import AsyncMock

    from pythinker.agent.tools.spawn import SpawnTool
    from pythinker.runtime.context import RequestContext

    manager = AsyncMock()
    manager.spawn = AsyncMock(return_value="ok")
    tool = SpawnTool(manager=manager)

    ctx_a = RequestContext.for_inbound(channel="a", sender_id="ua", chat_id="ca", session_key="a:ca")
    ctx_b = RequestContext.for_inbound(channel="b", sender_id="ub", chat_id="cb", session_key="b:cb")

    async def _run_with(ctx: RequestContext):
        tool.set_request_context(ctx)
        tool.set_egress(object())
        await asyncio.sleep(0.01)
        await tool.execute(task="t")

    task_a = asyncio.create_task(_run_with(ctx_a), context=contextvars.copy_context())
    task_b = asyncio.create_task(_run_with(ctx_b), context=contextvars.copy_context())
    await asyncio.gather(task_a, task_b)

    seen_ctxs = {call.kwargs["parent_context"].channel for call in manager.spawn.call_args_list}
    assert seen_ctxs == {"a", "b"}, f"ContextVar leaked across tasks: {seen_ctxs}"


async def test_subagent_announce_carries_parent_identity_in_context_seed(tmp_path):
    """Late completions must rebuild a governed context with the parent's identity.

    Regression: _announce_result published an InboundMessage without
    context_seed, so AgentLoop._attach_context fell back to msg.channel
    ("system") and msg.sender_id ("subagent") to synthesize a fresh
    RequestContext. The completion then ran with system/subagent/default
    identity instead of the parent's governed context.
    """
    from pythinker.agent.subagent import SubagentManager
    from pythinker.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"

    sm = SubagentManager(
        provider=provider, workspace=tmp_path, bus=bus,
        max_tool_result_chars=4096, model="m",
    )

    parent_ctx = RequestContext.for_inbound(
        channel="slack", sender_id="U_alice", chat_id="C_general", session_key="slack:C_general",
    )

    await sm._announce_result(
        task_id="abc12345",
        label="research",
        task="figure it out",
        result="done",
        origin={
            "channel": "slack",
            "chat_id": "C_general",
            "session_key": "slack:C_general",
            "sender_id": parent_ctx.sender_id,
        },
        status="ok",
    )

    # Drain inbound queue to read the published announcement.
    msg = await bus.inbound.get()
    assert msg.context_seed is not None
    assert msg.context_seed["channel"] == "slack"
    assert msg.context_seed["sender_id"] == "U_alice"
    assert msg.context_seed["chat_id"] == "C_general"
    # Outer routing fields preserved (system/subagent) so injected_event
    # detection in AgentLoop._dispatch is unchanged.
    assert msg.channel == "system"
    assert msg.sender_id == "subagent"
