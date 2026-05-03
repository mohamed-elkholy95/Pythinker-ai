import json
from pathlib import Path

import pytest

from pythinker.config.editing import (
    SECRET_PLACEHOLDER,
    ConfigEditError,
    collect_env_references,
    collect_field_defaults,
    read_config_value,
    redacted_config,
    save_config_with_backup,
    set_config_value,
    unset_config_value,
)
from pythinker.config.loader import load_config
from pythinker.config.schema import Config


def test_redacted_config_hides_secret_values_without_mutating_config() -> None:
    cfg = Config.model_validate(
        {
            "channels": {"websocket": {"tokenIssueSecret": "issue-secret"}},
            "tools": {
                "mcpServers": {
                    "private": {
                        "transport": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer secret"},
                    }
                }
            },
        }
    )
    cfg.providers.openai.api_key = "sk-live"
    cfg.providers.openai.extra_headers = {"X-Api-Key": "header-secret"}

    payload = redacted_config(cfg)

    assert payload["config"]["providers"]["openai"]["apiKey"] == SECRET_PLACEHOLDER
    assert payload["config"]["providers"]["openai"]["extraHeaders"] == SECRET_PLACEHOLDER
    assert payload["config"]["channels"]["websocket"]["tokenIssueSecret"] == SECRET_PLACEHOLDER
    assert payload["config"]["tools"]["mcpServers"]["private"]["headers"] == SECRET_PLACEHOLDER
    assert "providers.openai.api_key" in payload["secret_paths"]
    assert "tools.mcp_servers.private.headers" in payload["secret_paths"]
    assert cfg.providers.openai.api_key == "sk-live"


def test_config_editing_accepts_snake_and_camel_paths_and_validates() -> None:
    cfg = Config()

    result = set_config_value(cfg, "agents.defaults.contextWindowTokens", 123_456)

    assert cfg.agents.defaults.context_window_tokens == 123_456
    assert result.path == "agents.defaults.contextWindowTokens"
    assert result.restart_required is True
    assert read_config_value(cfg, "agents.defaults.context_window_tokens") == 123_456


def test_config_editing_rejects_unknown_or_private_fields() -> None:
    cfg = Config()

    with pytest.raises(ConfigEditError):
        read_config_value(cfg, "__class__")

    with pytest.raises(ConfigEditError):
        set_config_value(cfg, "agents.defaults.__dict__", "bad")


def test_config_editing_unset_restores_schema_default() -> None:
    cfg = Config()
    cfg.agents.defaults.model = "custom/model"

    unset_config_value(cfg, "agents.defaults.model")

    assert cfg.agents.defaults.model == Config().agents.defaults.model


def test_save_config_with_backup_writes_valid_config_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agents": {"defaults": {"model": "old/model"}}}))
    cfg = load_config(path)
    cfg.agents.defaults.model = "new/model"

    backup = save_config_with_backup(cfg, path)

    assert backup is not None
    assert backup.exists()
    assert json.loads(backup.read_text())["agents"]["defaults"]["model"] == "old/model"
    assert load_config(path).agents.defaults.model == "new/model"


def test_collect_env_references_reports_names_without_values() -> None:
    raw = {
        "providers": {"openai": {"apiKey": "${ADMIN_TEST_OPENAI_KEY}"}},
        "logging": {"level": "${ADMIN_TEST_LOG_LEVEL}", "json": False},
    }

    refs = collect_env_references(raw, {"providers.openai.api_key"})

    assert refs == {
        "providers.openai.api_key": {"env_var": "ADMIN_TEST_OPENAI_KEY", "is_secret": True},
        "logging.level": {"env_var": "ADMIN_TEST_LOG_LEVEL", "is_secret": False},
    }
    assert "sk-" not in json.dumps(refs)


def test_collect_field_defaults_flattens_schema_defaults() -> None:
    defaults = collect_field_defaults(Config.model_json_schema(by_alias=True))

    assert defaults["logging.level"] == Config().logging.level
    assert defaults["tools.web.browser.enable"] is False
    assert "providers.openai.api_key" in defaults
