"""Tests for the multi-agent path helpers.

Phase 2 onboard PR-1: ``current_agent_id`` resolves env var → marker
file → ``"default"``; ``agent_config_path`` falls back to the legacy
single-config path when the per-agent dir doesn't exist.
"""

from __future__ import annotations

import pytest

from pythinker.config import paths as paths_mod


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Pin ``Path.home()`` to a tmp dir so tests can write a marker file."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PYTHINKER_AGENT_ID", raising=False)
    yield


def test_current_agent_id_defaults_when_unset():
    assert paths_mod.current_agent_id() == "default"


def test_current_agent_id_reads_env(monkeypatch):
    monkeypatch.setenv("PYTHINKER_AGENT_ID", "research")
    assert paths_mod.current_agent_id() == "research"


def test_current_agent_id_empty_env_falls_through(monkeypatch, tmp_path):
    """An empty / whitespace env var must fall through to the marker file."""
    monkeypatch.setenv("PYTHINKER_AGENT_ID", "  ")
    marker = tmp_path / ".pythinker" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text("coding\n", encoding="utf-8")
    assert paths_mod.current_agent_id() == "coding"


def test_current_agent_id_reads_marker_file(tmp_path):
    marker = tmp_path / ".pythinker" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text("coding\n", encoding="utf-8")
    assert paths_mod.current_agent_id() == "coding"


def test_current_agent_id_env_wins_over_marker(monkeypatch, tmp_path):
    marker = tmp_path / ".pythinker" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text("coding", encoding="utf-8")
    monkeypatch.setenv("PYTHINKER_AGENT_ID", "research")
    assert paths_mod.current_agent_id() == "research"


def test_current_agent_id_blank_marker_falls_to_default(tmp_path):
    marker = tmp_path / ".pythinker" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text("   \n", encoding="utf-8")
    assert paths_mod.current_agent_id() == "default"


def test_current_agent_id_unreadable_marker_falls_to_default(tmp_path):
    """A marker dir-not-file is treated like missing."""
    marker = tmp_path / ".pythinker" / "current-agent"
    marker.mkdir(parents=True)  # it's a dir, not a file
    assert paths_mod.current_agent_id() == "default"


def test_agent_dir_returns_per_agent_path(tmp_path):
    expected = tmp_path / ".pythinker" / "agents" / "research"
    assert paths_mod.agent_dir("research") == expected


def test_agent_config_path_falls_back_to_legacy_when_dir_absent(tmp_path):
    """No per-agent dir → legacy ~/.pythinker/config.json."""
    legacy = tmp_path / ".pythinker" / "config.json"
    assert paths_mod.agent_config_path("research") == legacy


def test_agent_config_path_uses_per_agent_when_dir_exists(tmp_path):
    per_agent = tmp_path / ".pythinker" / "agents" / "research"
    per_agent.mkdir(parents=True)
    assert paths_mod.agent_config_path("research") == per_agent / "config.json"


def test_get_config_path_threads_through_agent_id(tmp_path, monkeypatch):
    """Loader's get_config_path uses agent_config_path when no override is set."""
    from pythinker.config import loader

    # Reset any prior set_config_path() call from earlier tests in the suite
    loader._current_config_path = None  # noqa: SLF001

    per_agent = tmp_path / ".pythinker" / "agents" / "research"
    per_agent.mkdir(parents=True)
    monkeypatch.setenv("PYTHINKER_AGENT_ID", "research")

    assert loader.get_config_path() == per_agent / "config.json"


def test_get_config_path_explicit_override_wins(tmp_path):
    """set_config_path() wins over agent-id resolution (used by --config)."""
    from pythinker.config import loader

    override = tmp_path / "explicit" / "config.json"
    loader.set_config_path(override)
    try:
        assert loader.get_config_path() == override
    finally:
        loader._current_config_path = None  # noqa: SLF001
