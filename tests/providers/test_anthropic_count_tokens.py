"""AnthropicCountTokensClient: cached, 429-aware token counter for Claude."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pythinker.providers.anthropic_count_tokens import (
    AnthropicCountTokensClient,
    CountTokensResult,
)


@pytest.mark.asyncio
async def test_count_returns_provider_input_tokens():
    transport = AsyncMock(
        return_value={"input_tokens": 2095, "context_management": {"original_input_tokens": 0}}
    )
    client = AnthropicCountTokensClient(transport=transport, cache_ttl_s=60)
    out = await client.count(
        messages=[{"role": "user", "content": "hello"}],
        system="be helpful",
        tools=None,
        model="claude-opus-4-7",
    )
    assert isinstance(out, CountTokensResult)
    assert out.input_tokens == 2095
    assert out.source == "anthropic_count_tokens"


@pytest.mark.asyncio
async def test_cache_hit_skips_network():
    transport = AsyncMock(return_value={"input_tokens": 50})
    client = AnthropicCountTokensClient(transport=transport, cache_ttl_s=60)
    msgs = [{"role": "user", "content": "x"}]
    await client.count(messages=msgs, system=None, tools=None, model="claude-opus-4-7")
    await client.count(messages=msgs, system=None, tools=None, model="claude-opus-4-7")
    assert transport.call_count == 1


@pytest.mark.asyncio
async def test_429_triggers_bounded_backoff_then_falls_back_to_local():
    transport = AsyncMock(side_effect=RuntimeError("429 rate_limit retry-after=3"))
    sleeper = AsyncMock()
    client = AnthropicCountTokensClient(
        transport=transport,
        sleep=sleeper,
        cache_ttl_s=60,
        max_retries=2,
    )
    out = await client.count(
        messages=[{"role": "user", "content": "x"}],
        system=None,
        tools=None,
        model="claude-opus-4-7",
    )
    assert out is None
    assert sleeper.await_count == 1


@pytest.mark.asyncio
async def test_cache_key_includes_model_system_tools():
    transport = AsyncMock(side_effect=[
        {"input_tokens": 10},
        {"input_tokens": 50},
        {"input_tokens": 200},
    ])
    client = AnthropicCountTokensClient(transport=transport, cache_ttl_s=60)
    msgs = [{"role": "user", "content": "x"}]
    a = await client.count(messages=msgs, system=None, tools=None, model="claude-opus-4-7")
    b = await client.count(messages=msgs, system="be helpful", tools=None, model="claude-opus-4-7")
    c = await client.count(
        messages=msgs,
        system=None,
        tools=[{"name": "t"}],
        model="claude-opus-4-7",
    )
    assert (a.input_tokens, b.input_tokens, c.input_tokens) == (10, 50, 200)
    assert transport.call_count == 3
