"""Trace-correlation test: every log record emitted from `_run_agent_loop`
must carry a `turn_id` extra so logs from concurrent turns can be sieved
back to their originating run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

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
async def test_run_agent_loop_binds_turn_id_to_logs(tmp_path):
    loop = _make_loop(tmp_path)

    captured_extras: list[dict] = []

    def sink(record):
        captured_extras.append(dict(record.record["extra"]))

    sink_id = logger.add(sink, filter=lambda r: "turn_id" in r["extra"])

    try:
        async def fake_run(spec: AgentRunSpec) -> AgentRunResult:
            return AgentRunResult(
                final_content="ok",
                messages=[],
                stop_reason="completed",
            )

        msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
        with patch.object(loop.runner, "run", side_effect=fake_run):
            await loop._run_agent_loop(
                initial_messages=[{"role": "user", "content": "hi"}],
                pending_queue=asyncio.Queue(),
                msg=msg,
            )
    finally:
        logger.remove(sink_id)

    # At least one log record must carry the bound turn_id extra.
    assert captured_extras, "expected at least one log record with turn_id extra"
    turn_ids = {extra["turn_id"] for extra in captured_extras}
    assert len(turn_ids) == 1, f"all logs in one turn must share a turn_id, got: {turn_ids}"
    only_turn_id = next(iter(turn_ids))
    assert isinstance(only_turn_id, str)
    assert len(only_turn_id) == 8  # uuid4().hex[:8]


@pytest.mark.asyncio
async def test_two_concurrent_turns_get_distinct_turn_ids(tmp_path):
    loop = _make_loop(tmp_path)

    captured_extras: list[dict] = []

    def sink(record):
        captured_extras.append(dict(record.record["extra"]))

    sink_id = logger.add(sink, filter=lambda r: "turn_id" in r["extra"])

    try:
        async def fake_run(spec: AgentRunSpec) -> AgentRunResult:
            return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

        msg1 = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="cli", sender_id="u2", chat_id="c2", content="b")

        with patch.object(loop.runner, "run", side_effect=fake_run):
            await asyncio.gather(
                loop._run_agent_loop(
                    initial_messages=[{"role": "user", "content": "a"}],
                    pending_queue=asyncio.Queue(),
                    channel="cli",
                    chat_id="c1",
                    msg=msg1,
                ),
                loop._run_agent_loop(
                    initial_messages=[{"role": "user", "content": "b"}],
                    pending_queue=asyncio.Queue(),
                    channel="cli",
                    chat_id="c2",
                    msg=msg2,
                ),
            )
    finally:
        logger.remove(sink_id)

    turn_ids = {extra["turn_id"] for extra in captured_extras}
    assert len(turn_ids) == 2, f"expected 2 distinct turn_ids, got: {turn_ids}"
