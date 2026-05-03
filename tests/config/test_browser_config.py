"""Tests for BrowserConfig defaults and nesting under WebToolsConfig."""

from pythinker.config.paths import get_browser_storage_dir
from pythinker.config.schema import BrowserConfig, WebToolsConfig


def test_browser_config_defaults():
    cfg = BrowserConfig()
    assert cfg.enable is False
    assert cfg.mode == "auto"
    assert cfg.cdp_url == "http://127.0.0.1:9222"
    assert cfg.headless is True
    assert cfg.executable_path is None
    assert cfg.auto_provision is True
    assert cfg.provision_timeout_s == 300
    assert cfg.default_timeout_ms == 15_000
    assert cfg.navigation_timeout_ms == 30_000
    assert cfg.eval_timeout_ms == 5_000
    assert cfg.snapshot_max_chars == 20_000
    assert cfg.idle_ttl_seconds == 600
    assert cfg.disconnect_on_idle is False
    assert cfg.max_pages_per_context == 5
    assert cfg.storage_state_dir is None


def test_browser_config_nested_in_web_tools_config():
    cfg = WebToolsConfig()
    assert isinstance(cfg.browser, BrowserConfig)
    assert cfg.browser.enable is False


def test_browser_config_camelcase_disk_alias():
    """Disk keys are camelCase; Python access is snake_case."""
    cfg = BrowserConfig.model_validate(
        {"enable": True, "cdpUrl": "http://example:9222"}
    )
    assert cfg.enable is True
    assert cfg.cdp_url == "http://example:9222"


def test_browser_config_mode_validation():
    cfg = BrowserConfig.model_validate({"enable": True, "mode": "launch"})
    assert cfg.mode == "launch"


def test_browser_config_signature_changes_for_lifecycle_fields():
    old = BrowserConfig(enable=True)
    new = BrowserConfig(enable=True, mode="cdp")
    assert old.signature() != new.signature()


def test_get_browser_storage_dir_under_data_dir(tmp_path, monkeypatch):
    from pythinker.config import paths

    # `get_runtime_subdir` calls `get_data_dir` then `ensure_dir`, so the
    # directory exists after the call. See pythinker/config/paths.py:11-19.
    monkeypatch.setattr(paths, "get_data_dir", lambda: tmp_path)
    result = get_browser_storage_dir()
    assert result == tmp_path / "browser"
    assert result.exists()
