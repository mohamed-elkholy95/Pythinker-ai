"""AgentLoop emits queue-depth + lock-wait telemetry under contention."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pythinker.bus.events import InboundMessage
from pythinker.bus.queue import MessageBus
from pythinker.runtime.context import RequestContext
from pythinker.runtime.telemetry import JSONLSink, set_sink


async def test_dispatch_emits_lock_wait_telemetry(tmp_path: Path):
    log = tmp_path / "loop.jsonl"
    set_sink(JSONLSink(log))
    try:
        from pythinker.agent.loop import AgentLoop

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "m"
        with patch("pythinker.agent.loop.ContextBuilder"), \
             patch("pythinker.agent.loop.SessionManager"), \
             patch("pythinker.agent.loop.SubagentManager") as mock_sub:
            mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
            loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

        async def _fake_process(msg, **kw):
            await asyncio.sleep(0.01)
            return None
        loop._process_message = _fake_process

        ctx = RequestContext.for_inbound(
            channel="cli", sender_id="u", chat_id="c", session_key="cli:c",
        )
        m1 = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="a", context=ctx)
        await loop._dispatch(m1)
    finally:
        set_sink(None)

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    names = [r["name"] for r in rows]
    assert "turn_started" in names
    started = next(r for r in rows if r["name"] == "turn_started")
    assert "lock_wait_s" in started["attributes"]
    assert "concurrency_wait_s" in started["attributes"]
    assert "inbound_queue_depth" in started["attributes"]
