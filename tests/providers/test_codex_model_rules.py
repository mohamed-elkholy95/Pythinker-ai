"""Codex OAuth allow-list and gpt-5.5 context-cap helpers.

Mirrors opencode's filter rules in packages/opencode/src/plugin/codex.ts so the
Codex provider rejects (or warns about) models the ChatGPT backend will refuse
and exposes the published gpt-5.5 limits to callers.
"""

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
