"""Codex OAuth allow-list and gpt-5.5 context-cap helpers.

Mirrors opencode's filter rules in packages/opencode/src/plugin/codex.ts so the
Codex provider rejects (or warns about) models the ChatGPT backend will refuse
and exposes the published gpt-5.5 limits to callers.
"""

from types import SimpleNamespace

import pytest

from pythinker.providers.openai_codex_provider import OpenAICodexProvider


def test_supported_models_includes_codex_variants():
    assert OpenAICodexProvider.is_supported_model("openai-codex/gpt-5.1-codex")
    assert OpenAICodexProvider.is_supported_model("gpt-5.1-codex-max")
    assert OpenAICodexProvider.is_supported_model("openai_codex/gpt-5.3-codex")


def test_supported_models_includes_explicit_allow_list():
    assert OpenAICodexProvider.is_supported_model("gpt-5.2")
    assert OpenAICodexProvider.is_supported_model("openai-codex/gpt-5.4-mini")


def test_supported_models_accepts_versions_above_5_4():
    # gpt-5.5+ are accepted via the regex branch (forward-compat).
    assert OpenAICodexProvider.is_supported_model("openai-codex/gpt-5.5")
    assert OpenAICodexProvider.is_supported_model("gpt-5.6")
    assert OpenAICodexProvider.is_supported_model("gpt-6.0")


def test_supported_models_rejects_old_versions():
    assert not OpenAICodexProvider.is_supported_model("openai-codex/gpt-4.1")
    assert not OpenAICodexProvider.is_supported_model("gpt-3.5-turbo")
    assert not OpenAICodexProvider.is_supported_model("openai-codex/gpt-5.0")


def test_supported_models_rejects_unknown_strings():
    assert not OpenAICodexProvider.is_supported_model("claude-opus-4.7")
    assert not OpenAICodexProvider.is_supported_model("openai-codex/random")


def test_gpt_5_5_limits_match_codex_plan():
    limits = OpenAICodexProvider.get_model_limits("openai-codex/gpt-5.5")
    assert limits == {"context": 400_000, "input": 272_000, "output": 128_000}


def test_get_model_limits_returns_none_for_uncapped():
    assert OpenAICodexProvider.get_model_limits("openai-codex/gpt-5.4") is None
    assert OpenAICodexProvider.get_model_limits("gpt-5.2") is None


def test_base_provider_returns_none_by_default():
    """LLMProvider base must expose the helper as a no-op for unknown providers."""
    from pythinker.providers.base import LLMProvider

    assert LLMProvider.get_model_limits("anything") is None


@pytest.mark.asyncio
async def test_prompt_cache_key_uses_stable_conversation_prefix(monkeypatch):
    """Only the stable system+first-user prefix should seed Codex prompt caching."""
    bodies: list[dict] = []

    monkeypatch.setattr(
        "pythinker.providers.openai_codex_provider._locked_get_codex_token",
        lambda: SimpleNamespace(account_id="acct", access="token"),
    )

    async def fake_request(url, headers, body, verify, on_content_delta=None):
        bodies.append(body)
        return "ok", [], "stop"

    monkeypatch.setattr("pythinker.providers.openai_codex_provider._request_codex", fake_request)

    provider = OpenAICodexProvider()
    await provider.chat([
        {"role": "system", "content": "You are pythinker."},
        {"role": "user", "content": "first request"},
        {"role": "assistant", "content": "first answer"},
    ])
    await provider.chat([
        {"role": "system", "content": "You are pythinker."},
        {"role": "user", "content": "first request"},
        {"role": "assistant", "content": "different answer"},
    ])
    await provider.chat([
        {"role": "system", "content": "You are pythinker."},
        {"role": "user", "content": "different request"},
        {"role": "assistant", "content": "first answer"},
    ])

    assert bodies[0]["prompt_cache_key"] == bodies[1]["prompt_cache_key"]
    assert bodies[0]["prompt_cache_key"] != bodies[2]["prompt_cache_key"]


@pytest.mark.asyncio
async def test_codex_reasoning_effort_none_omits_reasoning_body(monkeypatch):
    bodies: list[dict] = []

    monkeypatch.setattr(
        "pythinker.providers.openai_codex_provider._locked_get_codex_token",
        lambda: SimpleNamespace(account_id="acct", access="token"),
    )

    async def fake_request(url, headers, body, verify, on_content_delta=None):
        bodies.append(body)
        return "ok", [], "stop"

    monkeypatch.setattr("pythinker.providers.openai_codex_provider._request_codex", fake_request)

    provider = OpenAICodexProvider()
    await provider.chat(
        [{"role": "user", "content": "hello"}],
        reasoning_effort="none",
    )

    assert "reasoning" not in bodies[0]
