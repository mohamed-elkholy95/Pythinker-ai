"""Helpers added by the MiniMax token-plan onboarding feature."""

from unittest.mock import patch

from pythinker.cli import onboard
from pythinker.cli.onboard import _PRE_KEY_HOOKS, _mask_token, _minimax_pre_key
from pythinker.cli.onboard_views.styles import ONBOARD_QUESTIONARY_STYLE
from pythinker.config.schema import ProviderConfig


def test_mask_token_long_value_shows_first4_dots_last4():
    assert _mask_token("sk-12345678abcdEFGH") == "sk-1...EFGH"


def test_mask_token_exactly_12_chars_is_long_enough_to_unmask():
    # 12 chars is the boundary — first 4 + ... + last 4
    assert _mask_token("AAAA1234ZZZZ") == "AAAA...ZZZZ"


def test_mask_token_short_value_returns_three_stars():
    assert _mask_token("short") == "***"
    assert _mask_token("") == "***"
    assert _mask_token("12345678901") == "***"  # 11 chars, still short


def test_mask_token_none_returns_three_stars():
    assert _mask_token(None) == "***"  # type: ignore[arg-type]


def _patch_select(answer: str):
    """Patch _select_with_back to return the given answer."""
    return patch("pythinker.cli.onboard._select_with_back", return_value=answer)


def test_visible_select_choices_keeps_done_visible_near_bottom():
    choices = [f"Field {index}" for index in range(12)] + ["[Done]"]

    start, visible, has_before, has_after = onboard._visible_select_choices(
        choices,
        selected_index=9,
    )

    assert start > 0
    assert has_before is True
    assert has_after is False
    assert visible[-1] == "[Done]"


def test_ellipsize_menu_text_keeps_long_token_rows_single_line():
    token_row = "Token: " + "*" * 80 + "W7c"
    out = onboard._ellipsize_menu_text(token_row, 32)
    assert out.endswith("…")
    assert len(out) == 32


def test_input_with_existing_uses_shared_questionary_style():
    class _Prompt:
        def ask(self):
            return "Keep existing value"

    class _Questionary:
        def __init__(self):
            self.select_kwargs = {}

        def select(self, *args, **kwargs):
            self.select_kwargs = kwargs
            return _Prompt()

    questionary = _Questionary()
    with patch("pythinker.cli.onboard._get_questionary", return_value=questionary):
        assert onboard._input_with_existing("Token", "existing", "str") is None

    assert questionary.select_kwargs["style"] is ONBOARD_QUESTIONARY_STYLE


def test_pre_key_hooks_registry_covers_both_minimax_flavors():
    assert "minimax" in _PRE_KEY_HOOKS
    assert "minimax_anthropic" in _PRE_KEY_HOOKS
    assert _PRE_KEY_HOOKS["minimax"] is _minimax_pre_key
    assert _PRE_KEY_HOOKS["minimax_anthropic"] is _minimax_pre_key


def test_minimax_pre_key_global_minimax_flavor():
    cfg = ProviderConfig()
    with _patch_select("Global (api.minimax.io)"):
        signup = _minimax_pre_key(cfg, provider_name="minimax")
    assert cfg.api_base == "https://api.minimax.io/v1"
    assert signup == "https://platform.minimax.io/user-center/payment/token-plan"


def test_minimax_pre_key_global_anthropic_flavor():
    cfg = ProviderConfig()
    with _patch_select("Global (api.minimax.io)"):
        signup = _minimax_pre_key(cfg, provider_name="minimax_anthropic")
    assert cfg.api_base == "https://api.minimax.io/anthropic"
    assert signup == "https://platform.minimax.io/user-center/payment/token-plan"


def test_minimax_pre_key_mainland_swaps_base_and_signup_url():
    cfg = ProviderConfig()
    with _patch_select("Mainland China (api.minimaxi.com)"):
        signup = _minimax_pre_key(cfg, provider_name="minimax")
    assert cfg.api_base == "https://api.minimaxi.com/v1"
    # IMPORTANT: Mainland users get redirected to platform.minimaxi.com
    assert signup == "https://platform.minimaxi.com/user-center/payment/token-plan"


def test_minimax_pre_key_mainland_anthropic_flavor_uses_minimaxi_base():
    cfg = ProviderConfig()
    with _patch_select("Mainland China (api.minimaxi.com)"):
        signup = _minimax_pre_key(cfg, provider_name="minimax_anthropic")
    assert cfg.api_base == "https://api.minimaxi.com/anthropic"
    assert signup == "https://platform.minimaxi.com/user-center/payment/token-plan"


def test_minimax_pre_key_defaults_region_from_existing_base():
    """If api_base already implies Mainland, default the picker to Mainland."""
    cfg = ProviderConfig(api_base="https://api.minimaxi.com/v1")
    captured = {}

    def fake_select(prompt, choices, default=None):
        captured["default"] = default
        return choices[0]  # arbitrary

    with patch("pythinker.cli.onboard._select_with_back", side_effect=fake_select):
        _minimax_pre_key(cfg, provider_name="minimax")
    assert captured["default"] == "Mainland China (api.minimaxi.com)"
