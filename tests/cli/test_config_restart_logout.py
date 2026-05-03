"""Tests for the pythinker-style commands ported in this batch:
``pythinker config get/set/unset``, ``pythinker restart gateway|api``,
``pythinker auth logout``.

Each test isolates state under ``tmp_path`` so the developer's real
``~/.pythinker/config.json`` and OAuth tokens are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pythinker.cli.commands import (
    _coerce_value,
    _pids_listening_on,
    _walk_config_path,
    app,
)
from pythinker.config.schema import Config

runner = CliRunner()


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect get_config_path() to a temp file seeded with defaults."""
    cfg_path = tmp_path / "config.json"
    cfg = Config()
    cfg_path.write_text(cfg.model_dump_json(by_alias=True, indent=2))
    monkeypatch.setattr(
        "pythinker.config.loader.get_config_path", lambda: cfg_path
    )
    return cfg_path


# ---------------------------------------------------------------------------
# config — _walk + _coerce unit tests
# ---------------------------------------------------------------------------


def test_walk_resolves_snake_case():
    cfg = Config()
    parent, leaf = _walk_config_path(cfg, "agents.defaults.model")
    assert leaf == "model"
    # Parent should be the AgentDefaults submodel
    assert hasattr(parent, "model")


def test_walk_resolves_camel_case():
    """Same path expressed camelCase (matches on-disk JSON keys) must work."""
    cfg = Config()
    parent, leaf = _walk_config_path(cfg, "agents.defaults.contextWindowTokens")
    assert hasattr(parent, "context_window_tokens")


def test_walk_unknown_segment_raises_with_position():
    cfg = Config()
    with pytest.raises(KeyError) as exc:
        _walk_config_path(cfg, "agents.notreal.model")
    assert "notreal" in exc.value.args[0]
    assert "position 1" in exc.value.args[0]


def test_coerce_parses_json_primitives():
    assert _coerce_value("true") is True
    assert _coerce_value("false") is False
    assert _coerce_value("42") == 42
    assert _coerce_value("3.14") == 3.14
    assert _coerce_value("null") is None
    assert _coerce_value('"hello"') == "hello"


def test_coerce_falls_back_to_raw_string():
    """Anything that isn't valid JSON stays a string — no crash."""
    assert _coerce_value("openai-codex/gpt-5.5") == "openai-codex/gpt-5.5"
    assert _coerce_value("bare string with spaces") == "bare string with spaces"


# ---------------------------------------------------------------------------
# config — CLI integration
# ---------------------------------------------------------------------------


def test_config_get_prints_json_value(tmp_config):
    result = runner.invoke(app, ["config", "get", "agents.defaults.model"])
    assert result.exit_code == 0
    # Output is JSON-encoded, so the default model name is quoted.
    assert result.stdout.strip().startswith('"')


def test_config_get_unknown_path_exits_1(tmp_config):
    result = runner.invoke(app, ["config", "get", "agents.bogus.field"])
    assert result.exit_code == 1
    assert "unknown config segment" in result.stdout


def test_config_get_dunder_attr_is_rejected(tmp_config):
    """SECURITY: ``hasattr(model, '__class__')`` is True on any Pydantic
    model. The walker must gate on declared model_fields so users can't
    surface Python internals through ``config get __class__`` / similar.
    Same gate covers ``model_config``, ``__dict__``, ``model_fields``,
    every dunder, and any framework attr that isn't a declared field.
    """
    for path in ("__class__", "__dict__", "model_config", "model_fields"):
        result = runner.invoke(app, ["config", "get", path])
        assert result.exit_code == 1, f"{path} leaked through walker: {result.stdout}"
        # Either the segment-walk or the leaf-read rejects it — both prove
        # the gate held. The important thing is exit 1, not a specific msg.
        assert (
            "unknown config segment" in result.stdout
            or "unknown leaf field" in result.stdout
        )


def test_config_set_dunder_attr_is_rejected(tmp_config):
    """SECURITY: same gate must apply to set, not just get — otherwise
    ``config set model_config something`` could mutate Pydantic's
    framework state and corrupt the schema."""
    result = runner.invoke(app, ["config", "set", "model_config", '{"x":1}'])
    assert result.exit_code == 1


def test_config_set_round_trip_persists_to_disk(tmp_config):
    """set → reload from disk → get returns the new value."""
    result = runner.invoke(app, ["config", "set", "logging.level", "WARNING"])
    assert result.exit_code == 0

    on_disk = json.loads(Path(tmp_config).read_text())
    assert on_disk["logging"]["level"] == "WARNING"

    follow_up = runner.invoke(app, ["config", "get", "logging.level"])
    assert follow_up.exit_code == 0
    assert "WARNING" in follow_up.stdout


def test_config_set_invalid_value_exits_with_validation_error(tmp_config):
    """Schema validation must fire on bad values — not at next gateway boot."""
    # logging.level is a Literal — "LOUD" is not in the allowed set.
    result = runner.invoke(app, ["config", "set", "logging.level", "LOUD"])
    assert result.exit_code == 1


def test_config_set_coerces_bool_from_string(tmp_config):
    """`config set foo true` must write a real bool, not the string "true"."""
    result = runner.invoke(
        app, ["config", "set", "updates.check", "false"]
    )
    assert result.exit_code == 0
    on_disk = json.loads(Path(tmp_config).read_text())
    assert on_disk["updates"]["check"] is False


def test_config_unset_resets_to_schema_default(tmp_config):
    """Set then unset returns the field to its declared default."""
    runner.invoke(app, ["config", "set", "logging.level", "WARNING"])
    result = runner.invoke(app, ["config", "unset", "logging.level"])
    assert result.exit_code == 0
    on_disk = json.loads(Path(tmp_config).read_text())
    assert on_disk["logging"]["level"] == "INFO"  # the schema default


def test_config_set_preserves_env_var_indirection_in_other_fields(tmp_config, monkeypatch):
    """SECURITY-CRITICAL: ``config set`` must not silently expand ${VAR}
    references stored elsewhere in the config and write the resolved
    literal back to disk — that would leak secrets into config.json.

    ``load_config`` deliberately leaves ``${VAR}`` strings alone (env-var
    expansion happens via a separate ``resolve_config_env_vars`` copy
    consumed by the runtime). This test pins that contract: editing one
    field must round-trip every other ``${VAR}`` field unchanged.
    """
    from pythinker.config.loader import load_config, save_config
    from pythinker.config.schema import WebSearchProviderConfig

    monkeypatch.setenv("TEST_TOKEN_DO_NOT_LEAK", "this-must-never-land-on-disk")

    cfg = load_config()
    cfg.tools.web.search.providers["tavily"] = WebSearchProviderConfig(
        api_key="${TEST_TOKEN_DO_NOT_LEAK}"
    )
    save_config(cfg, tmp_config)

    # Now run an unrelated set on a different field.
    result = runner.invoke(app, ["config", "set", "logging.level", "WARNING"])
    assert result.exit_code == 0

    on_disk = json.loads(Path(tmp_config).read_text())
    leaked = on_disk["tools"]["web"]["search"]["providers"]["tavily"]["apiKey"]
    assert leaked == "${TEST_TOKEN_DO_NOT_LEAK}", (
        f"SECRET LEAK: ${{VAR}} got expanded to literal {leaked!r} on disk"
    )


# ---------------------------------------------------------------------------
# restart — port→pid resolver + CLI dry-run
# ---------------------------------------------------------------------------


def test_pids_listening_on_returns_empty_for_unbound_port():
    # Port 0 is reserved and never bound; safe negative test.
    assert _pids_listening_on(0) == []


def test_restart_gateway_no_start_exits_after_stop(tmp_path, monkeypatch):
    """--no-start should free the port and exit, not exec into a fresh gateway."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(Config().model_dump_json(by_alias=True))
    monkeypatch.setattr(
        "pythinker.config.loader.get_config_path", lambda: cfg_path
    )

    with patch(
        "pythinker.cli.commands._stop_service", return_value=True,
    ) as stop, patch("os.execvp") as handoff:
        result = runner.invoke(app, ["restart", "gateway", "--no-start"])

    assert result.exit_code == 0
    stop.assert_called_once()
    handoff.assert_not_called()


def test_restart_gateway_handoff_argv_shape(tmp_path, monkeypatch):
    """When stop succeeds, exec the gateway with the right argv (incl. --port)."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(Config().model_dump_json(by_alias=True))
    monkeypatch.setattr(
        "pythinker.config.loader.get_config_path", lambda: cfg_path
    )

    with patch(
        "pythinker.cli.commands._stop_service", return_value=True,
    ), patch("os.execvp") as handoff:
        runner.invoke(app, ["restart", "gateway", "--port", "19999"])

    handoff.assert_called_once()
    args, _kwargs = handoff.call_args
    assert "--port" in args[1]
    assert "19999" in args[1]


def test_restart_gateway_stop_failure_exits_non_zero(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(Config().model_dump_json(by_alias=True))
    monkeypatch.setattr(
        "pythinker.config.loader.get_config_path", lambda: cfg_path
    )

    with patch(
        "pythinker.cli.commands._stop_service", return_value=False,
    ), patch("os.execvp") as handoff:
        result = runner.invoke(app, ["restart", "gateway"])

    assert result.exit_code == 1
    handoff.assert_not_called()


# ---------------------------------------------------------------------------
# auth logout — error paths + token-file removal
# ---------------------------------------------------------------------------


def test_auth_logout_unknown_provider_exits_1():
    result = runner.invoke(app, ["auth", "logout", "definitely_not_a_provider"])
    assert result.exit_code == 1
    assert "unknown provider" in result.stdout


def test_auth_logout_non_oauth_provider_routes_to_config_unset():
    """Telling a user to logout of an API-key provider should point them at
    `config unset providers.<name>.api_key` instead of doing nothing."""
    result = runner.invoke(app, ["auth", "logout", "anthropic"])
    assert result.exit_code == 1
    # Rich may wrap the suggestion across lines; assert the two halves
    # rather than the full literal.
    assert "config" in result.stdout
    assert "providers.anthropic.api_key" in result.stdout


def test_auth_logout_no_token_file_is_noop(tmp_path):
    """Missing token file is a clean no-op — no crash, no confirmation prompt."""
    token_path = tmp_path / "absent.json"
    with patch(
        "pythinker.cli.commands._oauth_token_path", return_value=token_path,
    ):
        result = runner.invoke(app, ["auth", "logout", "openai_codex", "-y"])
    assert result.exit_code == 0
    # typer/rich wraps long lines, so collapse whitespace before substring checks.
    assert "nothing to do" in " ".join(result.stdout.split())


def test_auth_logout_unlinks_token_file_with_yes_flag(tmp_path):
    """-y skips the confirmation prompt and removes the token file."""
    token_path = tmp_path / "tok.json"
    token_path.write_text('{"access": "secret"}')
    with patch(
        "pythinker.cli.commands._oauth_token_path", return_value=token_path,
    ):
        result = runner.invoke(app, ["auth", "logout", "openai_codex", "-y"])
    assert result.exit_code == 0
    assert not token_path.exists()
    assert "logged out" in result.stdout
