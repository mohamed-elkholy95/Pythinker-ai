from unittest.mock import MagicMock

from pythinker.providers.limits import clamp_context_window, derive_window


def _provider(model_limits: dict[str, int] | None = None) -> MagicMock:
    provider = MagicMock()
    provider.get_model_limits.return_value = model_limits
    return provider


def test_unset_configured_falls_back_to_profile_input():
    provider = _provider(model_limits=None)
    window = derive_window(provider, "openai-codex/gpt-5.5", configured=None)
    assert window == 272_000


def test_explicit_configured_within_cap_kept_as_is():
    provider = _provider(model_limits={"input": 272_000})
    window = derive_window(provider, "openai-codex/gpt-5.5", configured=150_000)
    assert window == 150_000


def test_explicit_configured_above_cap_clamped_down():
    provider = _provider(model_limits={"input": 272_000})
    window = derive_window(provider, "openai-codex/gpt-5.5", configured=500_000)
    assert window == 272_000


def test_unknown_model_falls_back_to_configured_then_legacy_default():
    provider = _provider(model_limits=None)
    window = derive_window(provider, "unknown/v0", configured=100_000)
    assert window == 100_000
    window_none = derive_window(provider, "unknown/v0", configured=None)
    assert window_none == 65_536


def test_backwards_compat_alias_still_clamps():
    provider = _provider(model_limits={"input": 200_000})
    assert clamp_context_window(provider, "gpt-5.5", 250_000) == 200_000
    assert clamp_context_window(provider, "gpt-5.5", 100_000) == 100_000
