"""Unit tests for the update-check helpers in ``pythinker.utils.update``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pythinker import __version__
from pythinker.utils import update as update_mod
from pythinker.utils.update import (
    PYPI_JSON_URL,
    InstallMethod,
    UpdateInfo,
    check_for_update,
    detect_install_method,
    parse_pypi_response,
    select_latest_version,
    suggested_upgrade_command,
    upgrade_command,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_pypi_response_orders_newest_first_and_skips_unparseable():
    payload = {
        "releases": {
            "0.1.0": [{"yanked": False}],
            "0.2.0": [{"yanked": False}],
            "0.1.5": [{"yanked": False}],
            "not-a-version": [{"yanked": False}],
        }
    }
    versions, yanked = parse_pypi_response(payload)
    assert versions == ["0.2.0", "0.1.5", "0.1.0"]
    assert yanked == {"0.1.0": False, "0.2.0": False, "0.1.5": False}


def test_parse_pypi_response_marks_yanked_when_any_file_yanked():
    payload = {
        "releases": {
            "0.1.0": [{"yanked": False}, {"yanked": True}],
            "0.2.0": [{"yanked": False}],
        }
    }
    versions, yanked = parse_pypi_response(payload)
    assert yanked == {"0.1.0": True, "0.2.0": False}
    assert versions[0] == "0.2.0"


def test_select_latest_version_skips_yanked_and_prereleases_by_default():
    versions = ["0.2.0a1", "0.1.5", "0.1.4", "0.1.3"]
    yanked = {"0.2.0a1": False, "0.1.5": True, "0.1.4": False, "0.1.3": False}
    assert select_latest_version(versions, yanked) == "0.1.4"


def test_select_latest_version_includes_prereleases_when_asked():
    versions = ["0.2.0a1", "0.1.5"]
    yanked = {"0.2.0a1": False, "0.1.5": False}
    assert select_latest_version(versions, yanked, include_prereleases=True) == "0.2.0a1"


def test_select_latest_version_returns_none_when_all_filtered():
    versions = ["0.2.0a1", "0.1.5"]
    yanked = {"0.2.0a1": False, "0.1.5": True}
    assert select_latest_version(versions, yanked) is None


# ---------------------------------------------------------------------------
# Install-method detection
# ---------------------------------------------------------------------------


def test_detect_install_method_returns_editable_for_source_checkout(tmp_path, monkeypatch):
    fake_init = tmp_path / "src" / "pythinker" / "__init__.py"
    fake_init.parent.mkdir(parents=True)
    fake_init.write_text("# editable")
    fake_module = type(sys)("pythinker")
    fake_module.__file__ = str(fake_init)
    monkeypatch.setitem(sys.modules, "pythinker", fake_module)
    assert detect_install_method() is InstallMethod.EDITABLE


def test_detect_install_method_uv_tool(tmp_path, monkeypatch):
    site_packages = tmp_path / "uv" / "tools" / "pythinker-ai" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    pkg_init = site_packages / "pythinker" / "__init__.py"
    pkg_init.parent.mkdir()
    pkg_init.write_text("")
    fake_module = type(sys)("pythinker")
    fake_module.__file__ = str(pkg_init)
    monkeypatch.setitem(sys.modules, "pythinker", fake_module)
    monkeypatch.setattr(update_mod, "_running_in_container", lambda: False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "uv" / "tools" / "pythinker-ai"))
    assert detect_install_method() is InstallMethod.UV_TOOL


def test_upgrade_command_routes_per_method():
    assert upgrade_command(InstallMethod.UV_TOOL) == ["uv", "tool", "upgrade", "pythinker-ai"]
    assert upgrade_command(InstallMethod.PIPX) == ["pipx", "upgrade", "pythinker-ai"]
    pip_cmd = upgrade_command(InstallMethod.PIP_VENV)
    assert pip_cmd[1:] == ["-m", "pip", "install", "--upgrade", "pythinker-ai"]
    # Refuses for unsafe methods
    assert upgrade_command(InstallMethod.PIP_SYSTEM) is None
    assert upgrade_command(InstallMethod.EDITABLE) is None
    assert upgrade_command(InstallMethod.CONTAINER) is None
    assert upgrade_command(InstallMethod.UNKNOWN) is None


def test_suggested_upgrade_command_always_returns_string():
    for m in InstallMethod:
        assert suggested_upgrade_command(m)


# ---------------------------------------------------------------------------
# target_install_command — exact-version installs per install method
# ---------------------------------------------------------------------------


def test_target_install_command_uv_tool_uses_reinstall():
    from pythinker.utils.update import target_install_command

    cmd = target_install_command(InstallMethod.UV_TOOL, "2.0.0")
    assert cmd == ["uv", "tool", "install", "--reinstall", "pythinker-ai==2.0.0"]


def test_target_install_command_pipx_uses_force():
    from pythinker.utils.update import target_install_command

    assert target_install_command(InstallMethod.PIPX, "2.0.0") == [
        "pipx", "install", "--force", "pythinker-ai==2.0.0",
    ]


def test_target_install_command_pip_venv_uses_force_reinstall():
    from pythinker.utils.update import target_install_command

    cmd = target_install_command(InstallMethod.PIP_VENV, "2.0.0")
    assert cmd is not None
    assert cmd[1:] == ["-m", "pip", "install", "--force-reinstall", "pythinker-ai==2.0.0"]


def test_target_install_command_refuses_unsafe_methods():
    """EDITABLE / CONTAINER / UNKNOWN must refuse — caller has to print only."""
    from pythinker.utils.update import target_install_command

    for m in (InstallMethod.PIP_SYSTEM, InstallMethod.EDITABLE,
              InstallMethod.CONTAINER, InstallMethod.UNKNOWN):
        assert target_install_command(m, "2.0.0") is None


def test_suggested_target_command_always_returns_string():
    """Even refused methods must give the user something they can copy-paste."""
    from pythinker.utils.update import suggested_target_command

    for m in InstallMethod:
        out = suggested_target_command(m, "2.0.0")
        assert out
        # Every suggestion should mention the version.
        assert "2.0.0" in out


def test_suggested_target_command_pip_system_uses_force_reinstall():
    """The system-pip suggestion must NOT silently drop --force-reinstall —
    that's the only path that overrides an existing pin."""
    from pythinker.utils.update import suggested_target_command

    out = suggested_target_command(InstallMethod.PIP_SYSTEM, "2.0.0")
    assert "--force-reinstall" in out


# ---------------------------------------------------------------------------
# check_for_update with mocked PyPI (httpx_mock from pytest-httpx)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch):
    """Redirect get_update_dir() to a temp dir so cache writes don't pollute the user."""
    monkeypatch.setattr(update_mod, "get_update_dir", lambda: tmp_path)
    return tmp_path


async def test_check_for_update_detects_newer_version(httpx_mock, isolated_cache, monkeypatch):
    httpx_mock.add_response(
        url=PYPI_JSON_URL,
        json={
            "info": {"version": "9.9.9"},
            "releases": {
                __version__: [{"yanked": False}],
                "9.9.9": [{"yanked": False}],
            },
        },
    )
    monkeypatch.setattr(update_mod, "detect_install_method", lambda: InstallMethod.UV_TOOL)
    info = await check_for_update(force_refresh=True)
    assert info.checked_ok
    assert info.latest == "9.9.9"
    assert info.update_available
    assert info.is_yanked is False
    assert info.install_method is InstallMethod.UV_TOOL
    # Cache should have been written
    cached_payload = json.loads((isolated_cache / "state.json").read_text())
    assert cached_payload["latest"] == "9.9.9"


async def test_check_for_update_up_to_date(httpx_mock, isolated_cache, monkeypatch):
    httpx_mock.add_response(
        url=PYPI_JSON_URL,
        json={
            "info": {"version": __version__},
            "releases": {__version__: [{"yanked": False}]},
        },
    )
    monkeypatch.setattr(update_mod, "detect_install_method", lambda: InstallMethod.PIP_VENV)
    info = await check_for_update(force_refresh=True)
    assert info.checked_ok
    assert info.update_available is False
    assert info.latest == __version__


async def test_check_for_update_marks_yanked(httpx_mock, isolated_cache, monkeypatch):
    httpx_mock.add_response(
        url=PYPI_JSON_URL,
        json={
            "info": {"version": "9.9.9"},
            "releases": {
                __version__: [{"yanked": True}],
                "9.9.9": [{"yanked": False}],
            },
        },
    )
    monkeypatch.setattr(update_mod, "detect_install_method", lambda: InstallMethod.UV_TOOL)
    info = await check_for_update(force_refresh=True)
    assert info.is_yanked is True
    assert info.latest == "9.9.9"


async def test_check_for_update_filters_prereleases(httpx_mock, isolated_cache, monkeypatch):
    httpx_mock.add_response(
        url=PYPI_JSON_URL,
        json={
            "info": {"version": "0.99.0a1"},
            "releases": {
                __version__: [{"yanked": False}],
                "0.99.0a1": [{"yanked": False}],
            },
        },
    )
    monkeypatch.setattr(update_mod, "detect_install_method", lambda: InstallMethod.UV_TOOL)
    info = await check_for_update(force_refresh=True, include_prereleases=False)
    # current __version__ is the only non-prerelease; nothing newer
    assert info.update_available is False
    assert info.latest == __version__


@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
async def test_check_for_update_network_failure(httpx_mock, isolated_cache, monkeypatch):
    # No response registered → pytest-httpx raises TimeoutException by default
    monkeypatch.setattr(update_mod, "detect_install_method", lambda: InstallMethod.UV_TOOL)
    info = await check_for_update(force_refresh=True, timeout_s=0.1)
    assert info.checked_ok is False
    assert info.error_kind == "network"
    # Failure cache TTL is the short one
    assert info.cache_expires_at - info.fetched_at <= update_mod.DEFAULT_FAILURE_TTL_S + 1


async def test_check_for_update_uses_cache_within_ttl(httpx_mock, isolated_cache, monkeypatch):
    # Pre-populate cache with a fresh entry
    cache_path = isolated_cache / "state.json"
    cache_path.write_text(
        json.dumps(
            {
                "current": __version__,
                "latest": "9.9.9",
                "update_available": True,
                "is_yanked": False,
                "install_method": "uv-tool",
                "checked_ok": True,
                "error_kind": None,
                "error_message": None,
                "fetched_at": 1_000_000.0,
                "cache_expires_at": 9_999_999_999.0,  # far future
                "from_cache": False,
                "last_notified": None,
            }
        )
    )
    info = await check_for_update(force_refresh=False)
    assert info.from_cache is True
    assert info.latest == "9.9.9"
    # Should not have made an HTTP request
    assert not httpx_mock.get_requests()


async def test_check_for_update_disabled_via_env(isolated_cache, monkeypatch):
    monkeypatch.setenv("PYTHINKER_NO_UPDATE_CHECK", "1")
    info = await check_for_update(force_refresh=True)
    assert info.checked_ok is False
    assert info.error_kind == "disabled"


# ---------------------------------------------------------------------------
# Helpers used by CLI
# ---------------------------------------------------------------------------


def test_format_banner_returns_none_when_up_to_date():
    info = UpdateInfo(
        current=__version__, latest=__version__, update_available=False, is_yanked=False,
        install_method=InstallMethod.UV_TOOL, checked_ok=True, error_kind=None,
        error_message=None, fetched_at=0.0, cache_expires_at=0.0, from_cache=False,
    )
    assert update_mod.format_banner(info) is None


def test_format_banner_yanked_takes_priority():
    info = UpdateInfo(
        current=__version__, latest="9.9.9", update_available=True, is_yanked=True,
        install_method=InstallMethod.UV_TOOL, checked_ok=True, error_kind=None,
        error_message=None, fetched_at=0.0, cache_expires_at=0.0, from_cache=False,
    )
    line = update_mod.format_banner(info)
    assert line is not None
    assert "yanked" in line.lower()
