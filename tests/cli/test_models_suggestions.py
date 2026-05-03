"""Regression tests for ``get_model_suggestions`` per-provider coverage."""

from __future__ import annotations

from pythinker.cli.models import RECOMMENDED_BY_PROVIDER, get_model_suggestions


def test_minimax_token_plan_models_are_suggested():
    """The linear wizard's default-model picker must surface the MiniMax
    token-plan models when the user picked the ``minimax`` provider.

    Regression: previously ``RECOMMENDED_BY_PROVIDER`` had no ``minimax``
    entry, so ``_step_default_model`` only offered Keep / Manual / Back —
    the user could not pick a token-plan model in the linear wizard.
    """
    suggestions = get_model_suggestions("", provider="minimax")
    assert "MiniMax-M2.7" in suggestions
    assert "MiniMax-M2.7-highspeed" in suggestions


def test_minimax_anthropic_token_plan_models_are_suggested():
    """Same coverage for the Anthropic-compatible MiniMax flavor."""
    suggestions = get_model_suggestions("", provider="minimax_anthropic")
    assert "MiniMax-M2.7" in suggestions
    assert "MiniMax-M2.7-highspeed" in suggestions


def test_token_plan_models_match_followup_tier_constants():
    """The legacy ``_minimax_followup_plan_tier_step`` and the linear
    wizard's catalog must surface the same model ids — otherwise users
    pick different models depending on which path they take.
    """
    from pythinker.cli.onboard import _TIER_TO_MODEL

    canonical = set(_TIER_TO_MODEL.values())
    for flavor in ("minimax", "minimax_anthropic"):
        seeds = set(RECOMMENDED_BY_PROVIDER[flavor])
        missing = canonical - seeds
        assert not missing, f"Catalog gap for {flavor}: missing {missing}"


# Plan-bearing providers — verified against Context7 (2026-04-29). Each
# parameterized case asserts the bare minimum the wizard must surface so
# a user on the corresponding subscription doesn't dead-end at "no
# suggestions" after picking the provider.

def test_zhipu_glm_coding_plan_models_are_suggested():
    """GLM Coding Plan FAQ: only glm-4.7, glm-4.6, glm-4.5, glm-4.5-air
    are accepted on api.z.ai/api/coding/paas/v4. Plus glm-5 on Max/Pro tiers.
    """
    suggestions = get_model_suggestions("", provider="zhipu")
    for required in ("glm-4.7", "glm-5", "glm-4.6", "glm-4.5", "glm-4.5-air"):
        assert required in suggestions, f"Missing GLM Coding Plan model: {required}"


def test_moonshot_kimi_models_are_suggested():
    """Moonshot Coding Plan exposes kimi-k2.5 and kimi-k2.6 via the
    Anthropic endpoint; the OpenAI-compat endpoint also accepts the K2
    preview ids and the moonshot-v1 legacy line.
    """
    suggestions = get_model_suggestions("", provider="moonshot")
    for required in ("kimi-k2.6", "kimi-k2.5", "kimi-k2-thinking"):
        assert required in suggestions, f"Missing Kimi model: {required}"


def test_kimi_coding_plan_models_are_suggested():
    """The Kimi (Coding) provider entry must surface coding-plan ids."""
    suggestions = get_model_suggestions("", provider="kimi_coding")
    assert "kimi-k2.6" in suggestions
    assert "kimi-k2.5" in suggestions


def test_deepseek_models_are_suggested():
    """DeepSeek V4 series (and legacy aliases until 2026-07-24)."""
    suggestions = get_model_suggestions("", provider="deepseek")
    for required in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"):
        assert required in suggestions, f"Missing DeepSeek model: {required}"


def test_dashscope_qwen_coding_plan_models_are_suggested():
    """Aliyun Bailian Coding Plan: qwen3-coder-plus is the headline model."""
    suggestions = get_model_suggestions("", provider="dashscope")
    for required in ("qwen3-coder-plus", "qwen-max", "qwen-plus"):
        assert required in suggestions, f"Missing DashScope model: {required}"
