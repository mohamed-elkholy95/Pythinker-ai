"""Tests for the tools.web.search legacy-shape migration."""

import json
from pathlib import Path

from pythinker.config.loader import migrate_websearch_legacy_keys
from pythinker.config.schema import (
    Config,
    WebSearchConfig,
    WebSearchProviderConfig,
)


def _cfg(**search_kwargs) -> Config:
    cfg = Config()
    cfg.tools.web.search = WebSearchConfig(**search_kwargs)
    return cfg


def test_migrates_legacy_brave_key_into_providers_slot():
    cfg = _cfg(provider="brave", api_key="BSA-legacy")
    changed = migrate_websearch_legacy_keys(cfg)
    assert changed is True
    assert cfg.tools.web.search.api_key == ""
    assert cfg.tools.web.search.providers["brave"].api_key == "BSA-legacy"


def test_migrates_legacy_searxng_base_url():
    cfg = _cfg(provider="searxng", base_url="https://searx.example.com")
    changed = migrate_websearch_legacy_keys(cfg)
    assert changed is True
    assert cfg.tools.web.search.base_url == ""
    assert (
        cfg.tools.web.search.providers["searxng"].base_url
        == "https://searx.example.com"
    )


def test_migration_is_idempotent():
    cfg = _cfg(provider="brave", api_key="BSA-legacy")
    migrate_websearch_legacy_keys(cfg)
    second_pass = migrate_websearch_legacy_keys(cfg)
    assert second_pass is False
    assert cfg.tools.web.search.providers["brave"].api_key == "BSA-legacy"


def test_new_shape_wins_when_both_present():
    cfg = _cfg(
        provider="brave",
        api_key="legacy",
        providers={"brave": WebSearchProviderConfig(api_key="new")},
    )
    changed = migrate_websearch_legacy_keys(cfg)
    assert changed is True  # cleared the legacy field
    assert cfg.tools.web.search.api_key == ""
    assert cfg.tools.web.search.providers["brave"].api_key == "new"


def test_duckduckgo_with_stray_legacy_secret_warns_and_keeps():
    cfg = _cfg(provider="duckduckgo", api_key="STRAY")
    from loguru import logger as _loguru

    captured: list[tuple[str, str]] = []

    def _sink(message):
        record = message.record
        captured.append((record["level"].name, record["message"]))

    _loguru.enable("pythinker")
    handler_id = _loguru.add(_sink, level="WARNING")
    try:
        changed = migrate_websearch_legacy_keys(cfg)
    finally:
        _loguru.remove(handler_id)
    assert changed is False
    assert cfg.tools.web.search.api_key == "STRAY"
    warning_msgs = [msg for level, msg in captured if level == "WARNING"]
    assert warning_msgs, "Expected at least one WARNING-level log record"
    assert any("duckduckgo" in msg.lower() for msg in warning_msgs)


def test_unknown_provider_with_stray_legacy_secret_warns_and_keeps():
    cfg = _cfg(provider="madeup", api_key="STRAY")
    from loguru import logger as _loguru

    captured: list[tuple[str, str]] = []

    def _sink(message):
        record = message.record
        captured.append((record["level"].name, record["message"]))

    _loguru.enable("pythinker")
    handler_id = _loguru.add(_sink, level="WARNING")
    try:
        changed = migrate_websearch_legacy_keys(cfg)
    finally:
        _loguru.remove(handler_id)
    assert changed is False
    assert cfg.tools.web.search.api_key == "STRAY"
    warning_msgs = [msg for level, msg in captured if level == "WARNING"]
    assert warning_msgs, "Expected at least one WARNING-level log record"
    assert any(
        "madeup" in msg.lower() or "apikey" in msg.lower() for msg in warning_msgs
    ), "WARNING message should reference the provider name or legacy field"


def test_no_change_when_legacy_fields_empty():
    cfg = _cfg(provider="brave")
    changed = migrate_websearch_legacy_keys(cfg)
    assert changed is False
    assert cfg.tools.web.search.providers == {}


def test_migrates_empty_provider_as_brave():
    """Empty provider string defaults to Brave at runtime; migration must
    mirror that default so a config with `provider="" + apiKey="X"` doesn't
    silently lose its key on first read."""
    cfg = _cfg(provider="", api_key="BSA-empty")
    changed = migrate_websearch_legacy_keys(cfg)
    assert changed is True
    assert cfg.tools.web.search.api_key == ""
    assert cfg.tools.web.search.providers["brave"].api_key == "BSA-empty"


def _write_legacy_config(tmp_path: Path, body: dict) -> Path:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(body), encoding="utf-8")
    return cfg_path


def test_load_config_eagerly_persists_after_migration(tmp_path):
    from pythinker.config.loader import load_config

    cfg_path = _write_legacy_config(
        tmp_path,
        {"tools": {"web": {"search": {"provider": "brave", "apiKey": "BSA-legacy"}}}},
    )
    cfg = load_config(cfg_path)

    assert cfg.tools.web.search.providers["brave"].api_key == "BSA-legacy"
    assert cfg.tools.web.search.api_key == ""

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    search_on_disk = on_disk["tools"]["web"]["search"]
    assert search_on_disk.get("apiKey", "") == ""
    assert search_on_disk["providers"]["brave"]["apiKey"] == "BSA-legacy"


def test_load_config_persist_failure_is_warning_not_fatal(tmp_path, monkeypatch):
    from loguru import logger as _loguru

    from pythinker.config import loader as loader_mod

    cfg_path = _write_legacy_config(
        tmp_path,
        {"tools": {"web": {"search": {"provider": "brave", "apiKey": "BSA-legacy"}}}},
    )

    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(loader_mod, "save_config", boom)

    captured: list[tuple[str, str]] = []

    def _sink(message):
        record = message.record
        captured.append((record["level"].name, record["message"]))

    _loguru.enable("pythinker")
    handler_id = _loguru.add(_sink, level="WARNING")
    try:
        cfg = loader_mod.load_config(cfg_path)
    finally:
        _loguru.remove(handler_id)

    assert cfg.tools.web.search.providers["brave"].api_key == "BSA-legacy"
    warning_msgs = [msg for level, msg in captured if level == "WARNING"]
    all_warning_text = " ".join(warning_msgs).lower()
    assert "could not persist" in all_warning_text or "read-only" in all_warning_text

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["tools"]["web"]["search"].get("apiKey") == "BSA-legacy"


def test_load_config_no_persist_when_nothing_to_migrate(tmp_path):
    from pythinker.config.loader import load_config

    cfg_path = _write_legacy_config(
        tmp_path,
        {
            "tools": {
                "web": {
                    "search": {
                        "provider": "brave",
                        "providers": {"brave": {"apiKey": "BSA-1"}},
                    }
                }
            }
        },
    )
    before = cfg_path.stat().st_mtime_ns
    load_config(cfg_path)
    after = cfg_path.stat().st_mtime_ns
    assert before == after  # not rewritten
