from __future__ import annotations

from typing import Any

import pytest

from pythinker.providers.anthropic_provider import AnthropicProvider


@pytest.mark.asyncio
async def test_async_estimate_prompt_tokens_uses_count_tokens_transport(monkeypatch):
    provider = AnthropicProvider(api_key="test-key", default_model="claude-opus-4-7")
    captured: dict[str, Any] = {}

    async def transport(*, body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"input_tokens": 321}

    monkeypatch.setattr(provider, "_post_count_tokens", transport)

    tokens, source = await provider.async_estimate_prompt_tokens(
        [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello"},
        ],
        [{"function": {"name": "search", "parameters": {"type": "object"}}}],
        None,
    )

    assert (tokens, source) == (321, "anthropic_count_tokens")
    assert captured["model"] == "claude-opus-4-7"
    assert captured["system"] == "be helpful"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["tools"] == [{"name": "search", "input_schema": {"type": "object"}}]
