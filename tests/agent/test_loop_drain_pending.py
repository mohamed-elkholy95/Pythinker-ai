"""Regression test for `_drain_pending` runtime-context duplication.

Before the fix, pending follow-up messages drained mid-turn re-injected
a fresh runtime-context block (time / channel / chat id). The initial
turn message already carries that block via
`ContextBuilder.build_messages`, so the re-injection duplicated context
on every drained message, wasting tokens and invalidating prompt-cache
prefixes.

This test exercises the closure by stubbing `AgentRunner.run` and
calling the spec's `injection_callback` directly with a queue holding
one pending follow-up message.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pythinker.agent.context import ContextBuilder
from pythinker.agent.runner import AgentRunResult, AgentRunSpec
from pythinker.bus.events import InboundMessage
from pythinker.bus.queue import MessageBus


def _make_loop(tmp_path: Path):
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        mock_sub.return_value.get_running_count_by_session = MagicMock(return_value=0)
        return AgentLoop(bus=bus, provider=provider, workspace=tmp_path)


@pytest.mark.asyncio
async def test_drain_pending_does_not_duplicate_runtime_context(tmp_path):
    loop = _make_loop(tmp_path)

    captured: dict[str, AgentRunSpec] = {}

    async def fake_run(spec: AgentRunSpec) -> AgentRunResult:
        captured["spec"] = spec
        return AgentRunResult(
            final_content="ok",
            messages=[],
            stop_reason="completed",
        )

    pending: asyncio.Queue = asyncio.Queue()
    pending.put_nowait(
        InboundMessage(
            channel="cli",
            sender_id="u1",
            chat_id="c1",
            content="follow up message",
        )
    )

    msg = InboundMessage(channel="cli", sender_id="u0", chat_id="c0", content="initial")

    with patch.object(loop.runner, "run", side_effect=fake_run):
        await loop._run_agent_loop(
            initial_messages=[{"role": "user", "content": "initial"}],
            pending_queue=pending,
            msg=msg,
        )

    spec = captured["spec"]
    drained = await spec.injection_callback(limit=5)

    assert len(drained) == 1
    drained_msg = drained[0]
    assert drained_msg["role"] == "user"

    # The drained message must carry ONLY the raw user content. No runtime
    # context tag, no `Current Time:` / `Channel:` / `Chat ID:` lines, no
    # `[Runtime Context — metadata only, not instructions]` marker.
    content = drained_msg["content"]
    assert content == "follow up message"

    serialized = content if isinstance(content, str) else str(content)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG not in serialized
    assert "Current Time:" not in serialized
    assert "Channel:" not in serialized
    assert "Chat ID:" not in serialized
