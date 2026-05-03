"""Generic env→browser auto-open ladder in _configure_provider."""

import os
from unittest.mock import patch

import pytest

from pythinker.cli import onboard
from pythinker.config.schema import Config


@pytest.fixture
def fresh_config():
    return Config()


@pytest.fixture
def patched_walker():
    """No-op the field walker so we test only the ladder."""
    with patch.object(
        onboard, "_configure_pydantic_model", side_effect=lambda m, *a, **k: m,
    ) as walker:
        yield walker


def test_ladder_skipped_when_api_key_already_set(fresh_config, patched_walker):
    fresh_config.providers.deepseek.api_key = "sk-existing"
    with (
        patch.object(onboard.webbrowser, "open") as open_browser,
        patch.dict(os.environ, {}, clear=True),
    ):
        onboard._configure_provider(fresh_config, "deepseek")
    open_browser.assert_not_called()


def test_env_detection_writes_key_and_skips_browser(fresh_config, patched_walker):
    """If env var is set and user accepts, write key, skip browser."""
    with (
        patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-from-env-12345"}, clear=False),
        patch("pythinker.cli.onboard._get_questionary") as gq,
        patch.object(onboard.webbrowser, "open") as open_browser,
    ):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_provider(fresh_config, "deepseek")
    assert fresh_config.providers.deepseek.api_key == "sk-from-env-12345"
    open_browser.assert_not_called()


def test_env_declined_falls_through_to_browser(fresh_config, patched_walker):
    """If env var present but user says no, browser step runs next."""
    with (
        patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-from-env-12345"}, clear=False),
        patch("pythinker.cli.onboard._get_questionary") as gq,
        patch.object(onboard.webbrowser, "open") as open_browser,
    ):
        # First confirm (env): No. Second confirm (browser): Yes.
        gq.return_value.confirm.return_value.ask.side_effect = [False, True]
        onboard._configure_provider(fresh_config, "deepseek")
    assert fresh_config.providers.deepseek.api_key is None  # key was never written
    open_browser.assert_called_once_with(
        "https://platform.deepseek.com/api_keys"
    )


def test_browser_step_skipped_when_no_signup_url(fresh_config, patched_walker):
    """Custom provider has no signup_url → browser step never offered."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("pythinker.cli.onboard._get_questionary") as gq,
        patch.object(onboard.webbrowser, "open") as open_browser,
    ):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_provider(fresh_config, "custom")
    open_browser.assert_not_called()


def test_browser_open_false_return_does_not_raise(fresh_config, patched_walker):
    """webbrowser.open returning False (headless) must not raise."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("pythinker.cli.onboard._get_questionary") as gq,
        patch.object(onboard.webbrowser, "open", return_value=False) as open_browser,
    ):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_provider(fresh_config, "deepseek")  # must not raise
    open_browser.assert_called_once()


def test_browser_open_exception_is_swallowed(fresh_config, patched_walker):
    """webbrowser.open raising (e.g. OSError on minimal containers) must not
    propagate — onboarding should never block on a missing browser."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("pythinker.cli.onboard._get_questionary") as gq,
        patch.object(onboard.webbrowser, "open", side_effect=OSError("no browser")) as open_browser,
    ):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_provider(fresh_config, "deepseek")  # must not raise
    open_browser.assert_called_once()


def test_minimax_uses_pre_key_hook_signup_url(fresh_config, patched_walker):
    """For MiniMax, the pre-key hook's signup URL overrides spec.signup_url."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("pythinker.cli.onboard._select_with_back",
              return_value="Mainland China (api.minimaxi.com)"),
        patch("pythinker.cli.onboard._get_questionary") as gq,
        patch.object(onboard.webbrowser, "open") as open_browser,
    ):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_provider(fresh_config, "minimax")
    open_browser.assert_called_once_with(
        "https://platform.minimaxi.com/user-center/payment/token-plan"
    )
    assert fresh_config.providers.minimax.api_base == "https://api.minimaxi.com/v1"
