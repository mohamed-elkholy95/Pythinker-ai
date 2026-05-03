"""Tests for `pythinker doctor`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pythinker.cli import doctor as doctor_module
from pythinker.cli.commands import app


@pytest.fixture(autouse=True)
def _pin_terminal_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _isolate_codex_cli_auth(monkeypatch):
    """Never let the real developer token leak into doctor checks."""
    from oauth_cli_kit import storage as _storage

    monkeypatch.setattr(_storage, "_try_import_codex_cli_token", lambda _path: None)


@pytest.fixture
def _tmp_pythinker_home(tmp_path, monkeypatch):
    """Point get_config_path() at a temp dir so tests don't depend on the host config."""
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    # Also force the workspace location regardless of what the config says
    monkeypatch.setattr(
        "pythinker.config.paths.get_workspace_path",
        lambda _w=None: workspace,
    )
    # Token dir pinned to tmp so every OAuth provider appears unauthenticated
    monkeypatch.setenv("OAUTH_CLI_KIT_TOKEN_PATH", str(tmp_path / "token_missing.json"))
    return config_path, workspace


def _write_default_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": "openai-codex/gpt-5.5",
                        "provider": "auto",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _run_doctor() -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    return result.exit_code, result.output


# ---------------------------------------------------------------------------
# Unit checks
# ---------------------------------------------------------------------------


def test_check_python_version_ok_on_current_interpreter():
    r = doctor_module._check_python_version()
    assert r.status == "ok"
    assert r.label == "Python"


def test_check_install_location_reports_warning_when_not_on_path(monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)
    r = doctor_module._check_install_location()
    assert r.status == "warn"
    assert "not on PATH" in r.detail
    assert r.fix


def test_check_install_location_ok_when_on_path(monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: "/usr/local/bin/pythinker")
    r = doctor_module._check_install_location()
    assert r.status == "ok"
    assert "/usr/local/bin/pythinker" in r.detail


def test_check_config_errors_when_missing(_tmp_pythinker_home):
    r = doctor_module._check_config()
    assert r.status == "error"
    assert "missing" in r.detail
    assert "pythinker onboard" in r.fix


def test_check_config_ok_when_present(_tmp_pythinker_home):
    config_path, _ = _tmp_pythinker_home
    _write_default_config(config_path)
    r = doctor_module._check_config()
    assert r.status == "ok"


def test_check_workspace_ok_when_writable(_tmp_pythinker_home):
    config_path, _ = _tmp_pythinker_home
    _write_default_config(config_path)
    r = doctor_module._check_workspace()
    assert r.status == "ok"


def test_check_browser_disabled_when_not_enabled(_tmp_pythinker_home):
    config_path, _ = _tmp_pythinker_home
    _write_default_config(config_path)
    results = doctor_module._check_browser()
    assert results[0].status == "ok"
    assert "disabled" in results[0].detail
    assert "web tools disabled" not in results[0].detail


def test_check_browser_disabled_when_web_tools_disabled(_tmp_pythinker_home):
    """The wider `tools.web.enable=False` path should report explicitly."""
    config_path, _ = _tmp_pythinker_home
    data = {
        "agents": {"defaults": {"model": "openai-codex/gpt-5.5", "provider": "auto"}},
        "tools": {"web": {"enable": False, "browser": {"enable": True}}},
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")

    results = doctor_module._check_browser()

    assert results[0].status == "ok"
    assert "web tools disabled" in results[0].detail


async def test_check_browser_skips_cdp_probe_when_invoked_in_event_loop(
    _tmp_pythinker_home,
):
    """Defensive guard: doctor must not raise if called from inside a running loop."""
    config_path, _ = _tmp_pythinker_home
    data = {
        "agents": {"defaults": {"model": "openai-codex/gpt-5.5", "provider": "auto"}},
        "tools": {
            "web": {
                "browser": {
                    "enable": True,
                    "mode": "cdp",
                    "cdpUrl": "http://browser:9222",
                }
            }
        },
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")

    results = doctor_module._check_browser()

    cdp_results = [r for r in results if r.label == "Browser CDP"]
    assert cdp_results, "CDP check should run for mode=cdp"
    assert cdp_results[0].status == "warn"
    assert "skipped" in cdp_results[0].detail.lower()


def test_check_browser_errors_when_playwright_package_missing(
    _tmp_pythinker_home,
    monkeypatch,
):
    config_path, _ = _tmp_pythinker_home
    data = {
        "agents": {"defaults": {"model": "openai-codex/gpt-5.5", "provider": "auto"}},
        "tools": {"web": {"browser": {"enable": True}}},
    }
    config_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(doctor_module.importlib.util, "find_spec", lambda name: None)

    results = doctor_module._check_browser()

    assert results[0].status == "error"
    assert "Playwright package" in results[0].detail


# ---------------------------------------------------------------------------
# End-to-end command
# ---------------------------------------------------------------------------


def test_doctor_exits_with_error_when_config_missing(_tmp_pythinker_home):
    exit_code, output = _run_doctor()
    assert exit_code == 1
    assert "Config" in output
    assert "missing" in output
    # The fix-hint must be present so the user knows what to do next.
    assert "pythinker onboard" in output


def test_doctor_default_provider_oauth_auth_error(_tmp_pythinker_home):
    config_path, _ = _tmp_pythinker_home
    _write_default_config(config_path)

    exit_code, output = _run_doctor()
    # Codex OAuth token is absent (temp path), so default-provider auth is an error.
    assert exit_code == 1
    assert "OpenAI Codex" in output
    assert "not authenticated" in output
    assert "pythinker provider login openai-codex" in output


def test_doctor_all_green_with_valid_token(_tmp_pythinker_home, monkeypatch):
    config_path, _ = _tmp_pythinker_home
    _write_default_config(config_path)

    # Fake an authenticated default-provider token by stubbing the safe_token helper.
    class _Tok:
        access = "x"
        refresh = "y"
        account_id = "acc"

    monkeypatch.setattr(doctor_module, "_safe_token", lambda _spec: _Tok())

    exit_code, output = _run_doctor()
    # May still be "warn" (2) if the test runner's pythinker isn't on PATH, but never error (1).
    assert exit_code in (0, 2)
    assert "error" not in output.lower() or "errors" not in output.lower()
    assert "OAuth token present" in output


def test_doctor_non_interactive_uses_plain_ascii(_tmp_pythinker_home, monkeypatch):
    """--non-interactive must swap Rich glyphs for plain ASCII markers so the
    output pipes cleanly into logs / CI artefacts.  Regression guard for the
    earlier shape where the flag was declared but did nothing."""
    config_path, _ = _tmp_pythinker_home
    _write_default_config(config_path)

    class _Tok:
        access = "x"
        refresh = "y"
        account_id = "acc"

    monkeypatch.setattr(doctor_module, "_safe_token", lambda _spec: _Tok())

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--non-interactive"])

    # Plain-ASCII status markers must be present.
    assert "[OK]" in result.output

    # The interactive-only fix-arrow glyph must not appear.
    assert "→" not in result.output

    # Unicode check-mark / circle / cross from the interactive branch must not
    # leak through either.
    for interactive_glyph in ("✓", "○", "✗"):
        assert interactive_glyph not in result.output, (
            f"non-interactive doctor leaked interactive glyph {interactive_glyph!r}"
        )
