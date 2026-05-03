"""Tests for the ``pythinker auth list`` and ``pythinker channels list`` commands.

Pin the contract that matters:

* ``auth list`` walks every provider in the registry, never triggers an OAuth
  flow (read-only), and surfaces per-provider state (AUTH/MISSING/ERROR/etc.).
* ``channels list`` walks every channel in the registry and reports
  enabled/configured state from on-disk config alone — no live gateway probe.
"""

from __future__ import annotations

from typing import NamedTuple

from typer.testing import CliRunner

from pythinker.cli.commands import _auth_state, _channel_state, app


class _FakeToken(NamedTuple):
    access: str
    account_id: str


class _ApiKeyCfg:
    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base


class _OAuthSpec:
    """Minimal duck-typed ProviderSpec for unit tests."""

    def __init__(self, name: str):
        self.name = name
        self.is_oauth = True
        self.is_local = False
        self.is_gateway = False
        self.is_direct = False
        self.env_key = ""
        self.label = name


class _ApiKeySpec:
    def __init__(self, name: str = "anthropic", env_key: str = "ANTHROPIC_API_KEY"):
        self.name = name
        self.env_key = env_key
        self.is_oauth = False
        self.is_local = False
        self.is_gateway = False
        self.is_direct = False
        self.label = name


# ---------------------------------------------------------------------------
# _auth_state — unit tests for the per-provider state resolver
# ---------------------------------------------------------------------------


def test_auth_state_oauth_authenticated(monkeypatch):
    """OAuth provider with a stored token returns AUTH + account_id."""
    fake = _FakeToken(access="abc", account_id="acct-123")
    import oauth_cli_kit
    monkeypatch.setattr(oauth_cli_kit, "get_token", lambda: fake)

    state, detail = _auth_state(_OAuthSpec("openai_codex"), provider_cfg=None)
    assert state == "AUTHENTICATED"
    assert detail == "acct-123"


def test_auth_state_oauth_no_stored_token(monkeypatch):
    """OAuth provider with no stored token returns MISSING — never raises."""
    import oauth_cli_kit
    monkeypatch.setattr(oauth_cli_kit, "get_token", lambda: None)

    state, detail = _auth_state(_OAuthSpec("openai_codex"), provider_cfg=None)
    assert state == "MISSING"


def test_auth_state_oauth_loader_raises_treated_as_missing(monkeypatch):
    """If the OAuth loader itself blows up, surface MISSING — not ERROR.

    Important: a stat-only listing must never propagate exceptions to the
    user's terminal. MISSING with the exception type as a hint is enough.
    """
    import oauth_cli_kit
    def _boom():
        raise FileNotFoundError("oauth.json")
    monkeypatch.setattr(oauth_cli_kit, "get_token", _boom)

    state, detail = _auth_state(_OAuthSpec("openai_codex"), provider_cfg=None)
    assert state == "MISSING"
    assert "FileNotFoundError" in detail


def test_auth_state_api_key_set():
    state, detail = _auth_state(_ApiKeySpec(), _ApiKeyCfg(api_key="sk-abcdef"))
    assert state == "AUTHENTICATED"
    assert "9 chars" in detail  # len("sk-abcdef") == 9


def test_auth_state_api_key_env_var_indirection():
    """${ENV_VAR} indirection counts as authenticated, not as a literal key."""
    state, detail = _auth_state(_ApiKeySpec(), _ApiKeyCfg(api_key="${ANTHROPIC_API_KEY}"))
    assert state == "AUTHENTICATED"
    assert "env var ANTHROPIC_API_KEY" in detail


def test_auth_state_api_key_missing():
    state, detail = _auth_state(_ApiKeySpec(env_key="ANTHROPIC_API_KEY"), _ApiKeyCfg())
    assert state == "MISSING"
    assert "ANTHROPIC_API_KEY" in detail


# ---------------------------------------------------------------------------
# _channel_state — unit tests for the per-channel state resolver
# ---------------------------------------------------------------------------


def test_channel_state_dict_disabled():
    state, _ = _channel_state({"enabled": False, "token": "anything"})
    assert state == "OFF"


def test_channel_state_dict_enabled_with_token():
    state, detail = _channel_state({"enabled": True, "token": "BOT-TOKEN"})
    assert state == "ON"
    assert detail == "token:set"


def test_channel_state_dict_enabled_with_env_token():
    state, detail = _channel_state({"enabled": True, "token": "${TELEGRAM_BOT_TOKEN}"})
    assert state == "ON"
    assert detail == "${TELEGRAM_BOT_TOKEN}"


def test_channel_state_enabled_without_credential_flagged():
    """Enabled but no token/webhook is suspicious — surface it instead of
    pretending the channel is fully configured."""
    state, detail = _channel_state({"enabled": True})
    assert state == "ON"
    assert "no credential" in detail


def test_channel_state_camelcase_token_field():
    """Schema is camelCase on disk; the resolver must read it from a dict."""
    state, _ = _channel_state({"enabled": True, "botToken": None, "token": "x"})
    assert state == "ON"


def test_channel_state_none_block():
    state, detail = _channel_state(None)
    assert state == "OFF"


# ---------------------------------------------------------------------------
# CLI integration — `pythinker auth list` / `pythinker channels list`
# ---------------------------------------------------------------------------


runner = CliRunner()


def test_cli_auth_list_runs_and_includes_all_provider_labels(monkeypatch):
    """Smoke test: command exits 0 and prints a table row for every provider
    in the registry. Pins that the walk doesn't silently skip entries."""
    from pythinker.providers.registry import PROVIDERS

    # Avoid hitting real OAuth storage during the integration test.
    monkeypatch.setattr("oauth_cli_kit.get_token", lambda: None)
    monkeypatch.setattr(
        "pythinker.providers.github_copilot_provider.get_github_copilot_login_status",
        lambda: None,
    )

    result = runner.invoke(app, ["auth", "list"])
    assert result.exit_code == 0
    for spec in PROVIDERS:
        # Some labels (e.g. "OpenAI Codex") are width-clipped by rich; match a
        # left-truncated prefix to stay tolerant of the table layout.
        prefix = spec.label.split()[0]
        assert prefix in result.stdout, f"{spec.label} missing from auth list output"


def test_cli_channels_list_runs_and_includes_all_channel_labels():
    from pythinker.channels.registry import discover_all

    result = runner.invoke(app, ["channels", "list"])
    assert result.exit_code == 0
    for _name, cls in discover_all().items():
        label = getattr(cls, "display_name", _name)
        prefix = label.split()[0]
        assert prefix in result.stdout, f"{label} missing from channels list output"
