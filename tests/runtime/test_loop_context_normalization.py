"""AgentLoop._normalize_context produces a stamped RequestContext for every inbound path.

Four paths must all converge on the same code path:
  1. Channel ingress (seed → context)
  2. process_direct (no seed; synthesized from kwargs)
  3. Cron-triggered job (synthesized "channel=cron")
  4. Heartbeat-triggered job (synthesized "channel=heartbeat")

Each must stamp budgets from Config.runtime and produce a non-None context.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pythinker.bus.queue import MessageBus
from pythinker.config.schema import RuntimeConfig


def _make_loop(tmp_path: Path, runtime: RuntimeConfig | None = None):
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
            runtime_config=runtime or RuntimeConfig(),
        )


def test_normalize_from_channel_seed_stamps_budgets(tmp_path):
    rt = RuntimeConfig(max_tool_calls_per_turn=42, max_wall_clock_s=10.0)
    loop = _make_loop(tmp_path, rt)
    ctx = loop._normalize_context(
        seed={"channel": "slack", "sender_id": "U1", "chat_id": "C1"},
        session_key="slack:C1",
    )
    assert ctx.channel == "slack"
    assert ctx.sender_id == "U1"
    assert ctx.chat_id == "C1"
    assert ctx.session_key == "slack:C1"
    assert ctx.budgets.max_tool_calls == 42
    assert ctx.budgets.max_wall_clock_s == 10.0
    assert len(ctx.trace_id) == 32


def test_normalize_for_process_direct_synthesizes_from_kwargs(tmp_path):
    loop = _make_loop(tmp_path)
    ctx = loop._normalize_context_for_direct(
        session_key="api:default",
        channel="api",
        sender_id="api-client",
        chat_id="default",
    )
    assert ctx.channel == "api"
    assert ctx.session_key == "api:default"
    assert ctx.recursion_depth == 0


def test_normalize_for_cron_uses_cron_channel(tmp_path):
    loop = _make_loop(tmp_path)
    ctx = loop._normalize_context_for_cron(job_id="dream", session_key="cron:dream")
    assert ctx.channel == "cron"
    assert ctx.sender_id == "system"
    assert ctx.chat_id == "dream"
    assert ctx.session_key == "cron:dream"


def test_normalize_for_heartbeat_uses_heartbeat_channel(tmp_path):
    loop = _make_loop(tmp_path)
    ctx = loop._normalize_context_for_heartbeat(session_key="heartbeat:default")
    assert ctx.channel == "heartbeat"
    assert ctx.sender_id == "system"
    assert ctx.session_key == "heartbeat:default"


def test_run_consumer_attaches_context_to_inbound_msg(tmp_path):
    """The bus consumer must attach the normalized context to msg.context before dispatch."""
    from pythinker.bus.events import InboundMessage

    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="cli", sender_id="u", chat_id="c", content="hi",
        context_seed={"channel": "cli", "sender_id": "u", "chat_id": "c"},
    )
    # Calling _attach_context directly (the run loop calls this between
    # consume_inbound and dispatch).
    loop._attach_context(msg)
    assert msg.context is not None
    assert msg.context.channel == "cli"


def test_normalize_falls_back_when_seed_missing(tmp_path):
    """Direct InboundMessage construction without a seed (legacy callers) gets a synthesized context."""
    from pythinker.bus.events import InboundMessage

    loop = _make_loop(tmp_path)
    msg = InboundMessage(channel="legacy", sender_id="u", chat_id="c", content="hi")
    loop._attach_context(msg)
    assert msg.context is not None
    assert msg.context.channel == "legacy"
    assert msg.context.sender_id == "u"


def test_attach_context_is_idempotent_when_already_populated(tmp_path):
    """If msg.context is already set (e.g. by process_direct before publishing),
    _attach_context must short-circuit. Re-normalizing would mint a fresh
    trace_id and break call-tree correlation across re-attach paths."""
    from pythinker.bus.events import InboundMessage
    from pythinker.runtime.context import RequestContext

    loop = _make_loop(tmp_path)
    pre_attached = RequestContext.for_inbound(
        channel="api", sender_id="api-client", chat_id="default", session_key="api:default",
    )
    msg = InboundMessage(
        channel="api", sender_id="api-client", chat_id="default", content="hi",
        context=pre_attached,
    )
    loop._attach_context(msg)
    assert msg.context is pre_attached  # not replaced — same object identity
    assert msg.context.trace_id == pre_attached.trace_id  # trace preserved


async def test_run_consumer_integration_attaches_context_before_dispatch(tmp_path):
    """End-to-end wiring test: a message published to the bus must reach
    _process_message with a stamped context. Guards against a future
    refactor that drops the _attach_context call from run()."""
    import asyncio

    from pythinker.bus.events import InboundMessage

    loop = _make_loop(tmp_path)
    seen: list = []

    async def _process(msg, **kw):
        seen.append(msg.context)
        return None
    loop._process_message = _process

    # Fire run() in the background, publish one message, then stop.
    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(InboundMessage(
            channel="cli", sender_id="u", chat_id="c", content="hi",
            context_seed={"channel": "cli", "sender_id": "u", "chat_id": "c"},
        ))
        # Give run() a chance to consume and dispatch.
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.01)
    finally:
        loop._running = False
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass

    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0].channel == "cli"
    assert seen[0].agent_id == "default"


# All process_direct integration tests (context forwarded to AgentRunSpec,
# ingress-deny → PermissionError, blocked-sender silent drop in run())
# live in tests/runtime/test_loop_runtime_wiring.py under Task 8.
# This file only covers _normalize_context* helpers in isolation — the
# helpers exist after Task 4b; the call sites that USE them in process_direct
# land in Task 8, alongside self.policy and AgentRunSpec.request_context.


def test_resolve_agent_uses_live_policy_version_without_registry(tmp_path):
    """Policy enabled + no registry must still stamp the live policy_version.

    Regression: _resolve_agent previously short-circuited to ("default", 0)
    whenever agent_registry was None — even when self.policy was active and
    incrementing its policy_version. That made every context claim
    policy_version=0 while the live policy authorised the request, breaking
    the audit story (telemetry said 'ungoverned' for governed traffic).
    """
    from pythinker.runtime.policy import PolicyService

    loop = _make_loop(tmp_path)
    loop.agent_registry = None
    loop.policy = PolicyService(enabled=True)  # no manifests; deny-default
    # PolicyService exposes policy_version=1 when enabled, 0 when disabled.
    assert loop.policy.policy_version == 1

    ctx = loop._normalize_context(
        seed={"channel": "slack", "sender_id": "U1", "chat_id": "C1"},
        session_key="slack:C1",
    )
    assert ctx.policy_version == loop.policy.policy_version
    assert ctx.policy_version == 1


def test_resolve_agent_returns_zero_when_policy_disabled(tmp_path):
    """Policy disabled (or absent) must still stamp 0 — the audit-trail floor."""
    from pythinker.runtime.policy import PolicyService

    loop = _make_loop(tmp_path)
    loop.agent_registry = None
    loop.policy = PolicyService(enabled=False)
    ctx = loop._normalize_context(
        seed={"channel": "cli", "sender_id": "u", "chat_id": "c"},
        session_key="cli:c",
    )
    assert ctx.policy_version == 0
