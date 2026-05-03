"""AgentLoop accepts a PolicyService + ToolEgressGateway and forwards them to the runner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pythinker.bus.queue import MessageBus
from pythinker.runtime.policy import PolicyService


def _make_loop(tmp_path: Path):
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
            policy=PolicyService(enabled=True, allowed_tools={"default": ["read_file"]}),
        )


def test_loop_accepts_policy_service(tmp_path):
    loop = _make_loop(tmp_path)
    assert loop.policy is not None
    assert loop.policy.enabled is True
    # Egress should have been built from policy + the loop's tool registry.
    assert loop.egress is not None
    assert loop.egress._policy is loop.policy
    assert loop.egress._registry is loop.tools


def test_loop_without_policy_falls_back_to_disabled(tmp_path):
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
    assert loop.policy.enabled is False
    assert loop.egress is not None


async def test_process_direct_forwards_request_context_to_runspec(tmp_path):
    """Now that AgentRunSpec.request_context exists (Task 7), verify
    process_direct's normalized context is plumbed all the way through."""
    from pythinker.agent.loop import AgentLoop
    from pythinker.agent.runner import AgentRunResult

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    captured: list = []

    async def _fake_run(spec):
        captured.append(spec)
        return AgentRunResult(final_content="ok", messages=[])

    with patch.object(loop.runner, "run", side_effect=_fake_run):
        await loop.process_direct(content="hi", session_key="api:default")

    assert len(captured) == 1
    ctx = captured[0].request_context
    assert ctx is not None, (
        "process_direct did not forward request_context to AgentRunSpec — "
        "Task 8 wiring is incomplete"
    )
    assert ctx.channel == "api"
    assert ctx.session_key == "api:default"


async def test_process_direct_raises_when_ingress_denied(tmp_path):
    """Direct/API callers see PermissionError when ingress policy denies them."""
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    policy = PolicyService(
        enabled=True,
        allowed_tools={"default": ["*"]},
        blocked_senders={"api:bad-client"},
    )
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            policy=policy,
        )
    with pytest.raises(PermissionError):
        await loop.process_direct(
            content="hi",
            session_key="api:default",
            sender_id="bad-client",
        )


async def test_run_consumer_drops_blocked_sender(tmp_path):
    """When ingress policy denies an inbound message, _process_message is NOT called."""
    import asyncio

    from pythinker.agent.loop import AgentLoop
    from pythinker.bus.events import InboundMessage

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    policy = PolicyService(
        enabled=True,
        allowed_tools={"default": ["*"]},
        blocked_senders={"slack:U999"},
    )
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub_mgr:
        mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            policy=policy,
        )

    seen: list = []

    async def _process(msg, **kw):
        seen.append(msg)
        return None
    loop._process_message = _process

    run_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(InboundMessage(
            channel="slack", sender_id="U999", chat_id="C1", content="hi",
            context_seed={"channel": "slack", "sender_id": "U999", "chat_id": "C1"},
        ))
        await asyncio.sleep(0.05)
    finally:
        loop._running = False
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass

    assert seen == []
