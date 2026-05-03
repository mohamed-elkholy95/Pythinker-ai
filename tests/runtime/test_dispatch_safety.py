"""Lock the existing _dispatch behaviour before Task 12.3 rewrites the locking block.

These tests must pass against an unchanged main, then must still pass after
Task 12's rewrite. Treat any failure post-rewrite as a regression.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pythinker.bus.events import InboundMessage
from pythinker.bus.queue import MessageBus


def _make_loop(tmp_path: Path):
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(bus=bus, provider=provider, workspace=tmp_path)


async def test_dispatch_cleans_up_pending_queue_on_normal_completion(tmp_path):
    loop = _make_loop(tmp_path)

    async def _process(msg, **kw):
        return None
    loop._process_message = _process
    loop._attach_context = lambda m: None  # context not required for this assertion

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    await loop._dispatch(msg)
    assert "cli:c" not in loop._pending_queues  # cleanup ran


async def test_dispatch_publishes_error_and_cleans_up_on_exception(tmp_path):
    """Real _dispatch behaviour: catches Exception, publishes a 'Sorry' outbound,
    then runs the finally cleanup. The exception does NOT propagate."""
    loop = _make_loop(tmp_path)

    async def _boom(msg, **kw):
        raise RuntimeError("boom")
    loop._process_message = _boom
    loop._attach_context = lambda m: None

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    await loop._dispatch(msg)  # MUST NOT raise
    assert "cli:c" not in loop._pending_queues

    # An error outbound should have been published.
    out = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=0.5)
    assert "error" in out.content.lower() or "sorry" in out.content.lower()


async def test_dispatch_drains_leftover_pending_back_to_bus(tmp_path):
    """If pending-queue messages arrive while _process_message is running,
    they must be re-published to the bus on cleanup, not silently dropped."""
    loop = _make_loop(tmp_path)

    async def _process(msg, **kw):
        # Simulate one leftover message arriving in the queue mid-flight.
        loop._pending_queues["cli:c"].put_nowait(InboundMessage(
            channel="cli", sender_id="u", chat_id="c", content="leftover",
        ))
        return None
    loop._process_message = _process
    loop._attach_context = lambda m: None

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    await loop._dispatch(msg)
    # The leftover should have been re-published.
    leftover = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=0.5)
    assert leftover.content == "leftover"


async def test_dispatch_preserves_pending_queue_for_mid_turn_injection(tmp_path):
    """While _process_message is running, follow-up messages must reach pending_queues[key]."""
    loop = _make_loop(tmp_path)
    barrier = asyncio.Event()
    seen: list = []

    async def _process(msg, **kw):
        # While we're "processing", record the queue we should see by name.
        seen.append("cli:c" in loop._pending_queues)
        await barrier.wait()
        return None
    loop._process_message = _process
    loop._attach_context = lambda m: None

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    task = asyncio.create_task(loop._dispatch(msg))
    await asyncio.sleep(0.01)
    # Mid-flight, the queue must be live and routable.
    assert "cli:c" in loop._pending_queues
    barrier.set()
    await task
    assert seen == [True]


async def test_dispatch_restores_runtime_checkpoint_on_cancellation(tmp_path):
    """When a turn is cancelled, the runtime checkpoint stored during tool execution
    must be materialized into session history before _dispatch returns.

    Strict version: counts calls to _restore_runtime_checkpoint and asserts >= 1.
    Without this, a rewrite that drops the checkpoint-restore branch and just
    re-raises CancelledError would still pass the test."""
    from pythinker.session.manager import Session

    loop = _make_loop(tmp_path)

    sess = Session(key="cli:c")
    sess.metadata["_runtime_checkpoint"] = {"phase": "after_tool", "messages": [{"role": "tool", "content": "result"}]}
    loop.sessions.get_or_create = lambda k: sess
    loop.sessions.save = lambda s, **kw: None

    restore_calls: list = []

    def _track_restore(session):
        restore_calls.append(session)
        return True

    loop._restore_runtime_checkpoint = _track_restore
    loop._clear_pending_user_turn = lambda s: None
    loop._attach_context = lambda m: None

    async def _process(msg, **kw):
        raise asyncio.CancelledError()
    loop._process_message = _process

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    with pytest.raises(asyncio.CancelledError):
        await loop._dispatch(msg)

    # Hard assertion: the cancellation handler must have invoked
    # _restore_runtime_checkpoint at least once with our session.
    assert len(restore_calls) >= 1, (
        "_dispatch did not call _restore_runtime_checkpoint on CancelledError — "
        "checkpoint-preservation contract broken"
    )
    assert restore_calls[0] is sess
