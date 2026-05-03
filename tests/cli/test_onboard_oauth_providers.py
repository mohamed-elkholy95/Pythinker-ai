"""Onboarding wizard surfaces OAuth providers and routes them to the login flow.

Regression: the wizard's [P] LLM Provider picker filtered is_oauth=True
out, so users had no way to discover the Codex / Copilot login paths
from the interactive flow. Now OAuth providers appear with an "(OAuth)"
suffix and trigger the login handler instead of an API-key prompt.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pythinker.cli.onboard import (
    _configure_provider,
    _get_provider_info,
    _get_provider_names,
)
from pythinker.config.schema import Config


def _clear_caches():
    _get_provider_info.cache_clear()


def test_oauth_providers_appear_in_provider_picker():
    """openai-codex and github-copilot must be in the provider picker."""
    _clear_caches()
    names = _get_provider_names()
    assert "openai_codex" in names
    assert "github_copilot" in names


def test_configure_provider_dispatches_oauth_login_for_codex():
    """Picking openai-codex must call the OAuth login handler, not key prompt."""
    _clear_caches()
    cfg = Config()
    fake_token = SimpleNamespace(access="t", account_id="user@x")
    with patch("oauth_cli_kit.get_token", return_value=None), \
         patch("oauth_cli_kit.login_oauth_interactive", return_value=fake_token) as mock_login, \
         patch("builtins.input", return_value=""):
        _configure_provider(cfg, "openai_codex")
    assert mock_login.called


def test_configure_provider_short_circuits_when_codex_token_already_present():
    """A live token must skip the interactive flow."""
    _clear_caches()
    cfg = Config()
    cached = SimpleNamespace(access="t", account_id="cached@x")
    with patch("oauth_cli_kit.get_token", return_value=cached), \
         patch("oauth_cli_kit.login_oauth_interactive") as mock_login, \
         patch("builtins.input", return_value=""):
        _configure_provider(cfg, "openai_codex")
    assert not mock_login.called


def test_configure_provider_dispatches_device_flow_for_copilot():
    _clear_caches()
    cfg = Config()
    fake_token = SimpleNamespace(access="ghp", account_id="ghuser")
    with patch(
        "pythinker.providers.github_copilot_provider.login_github_copilot",
        return_value=fake_token,
    ) as mock_login, \
         patch("builtins.input", return_value=""):
        _configure_provider(cfg, "github_copilot")
    assert mock_login.called


def test_oauth_login_uses_plain_input_not_nested_questionary():
    """Regression: nested questionary inside an active picker fights for the
    TTY and aborts silently — sending users back to "Custom" with no error.
    The OAuth callback must use plain `input()` instead.
    """
    _clear_caches()
    cfg = Config()
    fake_token = SimpleNamespace(access="t", account_id="x")
    captured: list = []

    def fake_login(*, print_fn, prompt_fn):
        # Exercise prompt_fn — it must NOT recurse into questionary.
        captured.append(prompt_fn("Code"))
        return fake_token

    with patch("oauth_cli_kit.get_token", return_value=None), \
         patch("oauth_cli_kit.login_oauth_interactive", side_effect=fake_login), \
         patch("builtins.input", return_value="user-code-123") as mock_input, \
         patch("pythinker.cli.onboard._get_questionary") as mock_q:
        _configure_provider(cfg, "openai_codex")

    # prompt_fn returned what input() returned, NOT what questionary returned.
    assert captured == ["user-code-123"]
    assert mock_input.called
    # questionary must not have been touched during the OAuth callback.
    mock_q.assert_not_called()


def test_oauth_login_pauses_for_input_before_returning():
    """Regression: the wizard's outer loop calls console.clear() on every
    iteration, wiping OAuth result before the user sees it. The handler must
    pause for Enter so the success/failure line survives the redraw.
    """
    _clear_caches()
    cfg = Config()
    fake_token = SimpleNamespace(access="t", account_id="x")
    input_calls: list = []

    def fake_input(prompt=""):
        input_calls.append(prompt)
        return ""

    with patch("oauth_cli_kit.get_token", return_value=None), \
         patch("oauth_cli_kit.login_oauth_interactive", return_value=fake_token), \
         patch("builtins.input", side_effect=fake_input):
        _configure_provider(cfg, "openai_codex")

    # At least one of the input() calls is the post-OAuth pause prompt.
    assert any("continue" in p.lower() or "press enter" in p.lower()
               for p in input_calls)


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="real questionary prompt instantiates a prompt_toolkit Application"
    " against a missing console buffer in CI on Windows",
)
def test_configure_provider_keyed_path_unchanged_for_openai():
    """Plain `openai` (API key) must NOT be routed to OAuth — it has no OAuth path."""
    _clear_caches()
    cfg = Config()
    # If routing accidentally landed on the OAuth flow, oauth_cli_kit would
    # be imported. Patch it to None so an accidental call would raise.
    with patch("oauth_cli_kit.login_oauth_interactive") as mock_oauth, \
         patch("pythinker.cli.onboard._get_questionary") as mock_q:
        # Make the wizard's confirm/text prompts deniable — we just want the
        # oauth handler to NOT have been invoked.
        mock_q.return_value.confirm.return_value.ask.return_value = False
        mock_q.return_value.text.return_value.ask.return_value = ""
        _configure_provider(cfg, "openai")
    mock_oauth.assert_not_called()
