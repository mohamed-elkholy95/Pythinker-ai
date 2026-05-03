"""`pythinker provider login <name>` — OAuth subcommand for codex + copilot.

The wizard's `[P] LLM Provider` picker filters OAuth providers because
they don't have an API key to prompt for. Without `provider login`,
users had no way to trigger the OAuth flow at all. This test pins the
subcommand's contract so the dead-end stays closed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from pythinker.cli.commands import app

runner = CliRunner()


def test_provider_login_unknown_provider_lists_supported_names():
    result = runner.invoke(app, ["provider", "login", "nonexistent"])
    assert result.exit_code == 1
    out = result.stdout
    assert "Unknown OAuth provider" in out
    # Both OAuth providers must be advertised in the help text.
    assert "openai-codex" in out
    assert "github-copilot" in out


def test_provider_login_rejects_non_oauth_provider():
    """openai (API-key) is intentionally not an OAuth provider — must reject."""
    result = runner.invoke(app, ["provider", "login", "openai"])
    assert result.exit_code == 1
    assert "Unknown OAuth provider" in result.stdout


def test_provider_login_openai_codex_invokes_oauth_kit():
    """openai-codex login must call oauth_cli_kit.login_oauth_interactive on missing token."""
    fake_token = SimpleNamespace(access="t-123", account_id="user@example.com")
    with patch("oauth_cli_kit.get_token", return_value=None), \
         patch("oauth_cli_kit.login_oauth_interactive", return_value=fake_token) as mock_login:
        result = runner.invoke(app, ["provider", "login", "openai-codex"])
    assert result.exit_code == 0
    assert mock_login.called
    assert "Authenticated with OpenAI Codex" in result.stdout


def test_provider_login_openai_codex_skips_login_when_token_present():
    """If a token already exists, don't restart the browser flow."""
    fake_token = SimpleNamespace(access="cached", account_id="cached@example.com")
    with patch("oauth_cli_kit.get_token", return_value=fake_token), \
         patch("oauth_cli_kit.login_oauth_interactive") as mock_login:
        result = runner.invoke(app, ["provider", "login", "openai-codex"])
    assert result.exit_code == 0
    assert not mock_login.called
    assert "Authenticated with OpenAI Codex" in result.stdout


def test_provider_login_github_copilot_invokes_device_flow():
    fake_token = SimpleNamespace(access="gh-token", account_id="ghuser")
    with patch(
        "pythinker.providers.github_copilot_provider.login_github_copilot",
        return_value=fake_token,
    ) as mock_login:
        result = runner.invoke(app, ["provider", "login", "github-copilot"])
    assert result.exit_code == 0
    assert mock_login.called
    assert "Authenticated with GitHub Copilot" in result.stdout


def test_provider_login_accepts_underscore_form():
    """Both 'openai-codex' and 'openai_codex' must resolve to the same handler."""
    fake_token = SimpleNamespace(access="t", account_id="x")
    with patch("oauth_cli_kit.get_token", return_value=fake_token), \
         patch("oauth_cli_kit.login_oauth_interactive"):
        result = runner.invoke(app, ["provider", "login", "openai_codex"])
    assert result.exit_code == 0


def test_provider_login_help_lists_subcommand():
    result = runner.invoke(app, ["provider", "--help"])
    assert result.exit_code == 0
    assert "login" in result.stdout


def test_provider_login_openai_codex_emits_ssh_hint():
    """openai-codex login must emit the SSH-awareness hint before the OAuth flow."""
    fake_token = SimpleNamespace(access="t-hint", account_id="u@x")
    with patch("oauth_cli_kit.get_token", return_value=None), \
         patch("oauth_cli_kit.login_oauth_interactive", return_value=fake_token):
        result = runner.invoke(app, ["provider", "login", "openai-codex"])
    assert result.exit_code == 0
    # The SSH hint must appear in the output so SSH users know paste is available.
    assert "SSH" in result.stdout or "headless" in result.stdout or "paste" in result.stdout.lower()


def test_provider_login_github_copilot_emits_device_flow_hint():
    """github-copilot login must emit the device-flow SSH hint."""
    fake_token = SimpleNamespace(access="gh-t", account_id="ghuser")
    with patch(
        "pythinker.providers.github_copilot_provider.login_github_copilot",
        return_value=fake_token,
    ):
        result = runner.invoke(app, ["provider", "login", "github-copilot"])
    assert result.exit_code == 0
    # Device-flow hint must tell the user they can use any device.
    assert "device" in result.stdout.lower() or "URL" in result.stdout or "code" in result.stdout.lower()
