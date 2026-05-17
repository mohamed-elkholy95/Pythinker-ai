"""Session lock coverage for prepare -> consolidate -> build and auto-compact."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.agent.loop import AgentLoop
from pythinker.bus.queue import MessageBus
from pythinker.providers.base import GenerationSettings, LLMResponse


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "gpt-5.5"
    provider.generation = GenerationSettings(max_tokens=4_096)
    provider.estimate_prompt_tokens = lambda *_a, **_kw: (10, "test")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    return provider


@pytest.mark.asyncio
async def test_inbound_path_does_not_deadlock(tmp_path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="gpt-5.5",
    )
    await asyncio.wait_for(loop.process_direct("hi", session_key="cli:t"), timeout=5.0)


@pytest.mark.asyncio
async def test_archive_waits_for_inflight_turn(tmp_path):
    provider = _provider()
    llm_started = asyncio.Event()
    llm_release = asyncio.Event()

    async def slow_llm(*_args, **_kwargs):
        llm_started.set()
        await llm_release.wait()
        return LLMResponse(content="ok", tool_calls=[])

    provider.chat_with_retry = AsyncMock(side_effect=slow_llm)
    provider.chat_stream_with_retry = AsyncMock(side_effect=slow_llm)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="gpt-5.5",
        context_window_tokens=10_000,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])

    archive_started = asyncio.Event()

    async def slow_archive(_msgs):
        archive_started.set()
        return "summary"

    loop.consolidator.archive = slow_archive  # type: ignore[assignment]

    key = "cli:t"
    session = loop.sessions.get_or_create(key)
    for _ in range(40):
        session.add_message("user", "u")
        session.add_message("assistant", "a")
    loop.sessions.save(session)

    turn = asyncio.create_task(loop.process_direct("hello", session_key=key))
    await asyncio.wait_for(llm_started.wait(), timeout=5.0)
    archive_task = asyncio.create_task(loop.auto_compact._archive(key))
    await asyncio.sleep(0.05)

    assert not archive_started.is_set()

    llm_release.set()
    await turn
    await archive_task
    assert archive_started.is_set()
