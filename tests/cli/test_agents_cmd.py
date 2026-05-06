"""Tests for the ``pythinker agents`` Typer subcommand.

Phase 2 PR-2 of `.agents/plans/2026-05-05-onboard-phase-2-multi-agent.md`.
Covers the three acceptance-gate items:

  * Round-trip: ``create research`` → ``switch research`` → resolved
    config path is the new per-agent config.
  * ``delete default`` is refused unconditionally.
  * ``delete <id>`` requires ``--confirm <id>`` and refuses without it.

Plus the corner cases the implementation guards: invalid ids, reserved
names, refusing to overwrite, refusing to delete the active agent.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pythinker.cli.agents import app as agents_app


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Pin ``Path.home()`` cross-platform; see ``tests/config/test_agent_paths.py``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", tmp_path.drive or "")
    monkeypatch.setenv("HOMEPATH", str(tmp_path).removeprefix(tmp_path.drive or ""))
    monkeypatch.delenv("PYTHINKER_AGENT_ID", raising=False)
    # Reset the loader's cached config-path override between tests.
    from pythinker.config import loader

    loader._current_config_path = None  # noqa: SLF001
    yield


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_list_empty(runner, tmp_path):
    result = runner.invoke(agents_app, ["list"])
    assert result.exit_code == 0
    assert "No agents found" in result.output


def test_list_legacy_default(runner, tmp_path):
    """A single ~/.pythinker/config.json shows up as 'default (legacy)'."""
    legacy = tmp_path / ".pythinker" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"agents":{"defaults":{"model":"openai-codex/gpt-5.5"}}}')
    result = runner.invoke(agents_app, ["list"])
    assert result.exit_code == 0
    assert "default (legacy)" in result.output
    assert "openai-codex/gpt-5.5" in result.output


def test_create_scaffolds_dir_and_config(runner, tmp_path):
    result = runner.invoke(agents_app, ["create", "research"])
    assert result.exit_code == 0, result.output
    target = tmp_path / ".pythinker" / "agents" / "research"
    assert target.is_dir()
    assert (target / "config.json").is_file()
    assert (target / "workspace").is_dir()


def test_create_refuses_to_overwrite(runner, tmp_path):
    target = tmp_path / ".pythinker" / "agents" / "coding"
    target.mkdir(parents=True)
    result = runner.invoke(agents_app, ["create", "coding"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_create_with_from_copies_config(runner, tmp_path):
    """`--from` copies the source agent's config.json + memory artifacts."""
    src_dir = tmp_path / ".pythinker" / "agents" / "research"
    src_dir.mkdir(parents=True)
    (src_dir / "config.json").write_text('{"copied":"yes"}')
    (src_dir / "workspace").mkdir()
    (src_dir / "workspace" / "MEMORY.md").write_text("seed memory")

    result = runner.invoke(agents_app, ["create", "coding", "--from", "research"])
    assert result.exit_code == 0, result.output

    new_dir = tmp_path / ".pythinker" / "agents" / "coding"
    assert (new_dir / "config.json").read_text() == '{"copied":"yes"}'
    assert (new_dir / "workspace" / "MEMORY.md").read_text() == "seed memory"


def test_create_with_missing_from_fails_clean(runner, tmp_path):
    result = runner.invoke(agents_app, ["create", "coding", "--from", "missing"])
    assert result.exit_code == 1
    # Failed copy must roll back the just-created dir.
    assert not (tmp_path / ".pythinker" / "agents" / "coding").exists()


def test_switch_writes_marker(runner, tmp_path):
    target = tmp_path / ".pythinker" / "agents" / "research"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}")

    result = runner.invoke(agents_app, ["switch", "research"])
    assert result.exit_code == 0, result.output
    marker = tmp_path / ".pythinker" / "current-agent"
    assert marker.read_text(encoding="utf-8").strip() == "research"


def test_switch_refuses_unknown_id(runner, tmp_path):
    result = runner.invoke(agents_app, ["switch", "nonexistent"])
    assert result.exit_code == 1
    assert "no config" in result.output.lower()


def test_switch_default_writes_marker_and_warns(runner, tmp_path):
    """`switch default` is allowed even without a per-agent dir."""
    result = runner.invoke(agents_app, ["switch", "default"])
    assert result.exit_code == 0, result.output
    marker = tmp_path / ".pythinker" / "current-agent"
    assert marker.read_text(encoding="utf-8").strip() == "default"


def test_round_trip_create_switch_resolves_per_agent_config(runner, tmp_path):
    """The acceptance-gate happy path: create + switch → loader sees new config."""
    from pythinker.config.loader import get_config_path

    runner.invoke(agents_app, ["create", "research"])
    runner.invoke(agents_app, ["switch", "research"])

    expected = tmp_path / ".pythinker" / "agents" / "research" / "config.json"
    assert get_config_path() == expected


def test_delete_default_is_refused(runner, tmp_path):
    result = runner.invoke(agents_app, ["delete", "default", "--confirm", "default"])
    assert result.exit_code == 1
    assert "reserved" in result.output.lower()


def test_delete_active_agent_is_refused(runner, tmp_path, monkeypatch):
    target = tmp_path / ".pythinker" / "agents" / "research"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}")
    monkeypatch.setenv("PYTHINKER_AGENT_ID", "research")

    result = runner.invoke(agents_app, ["delete", "research", "--confirm", "research"])
    assert result.exit_code == 1
    assert "currently-active" in result.output.lower()


def test_delete_without_confirm_is_refused(runner, tmp_path):
    target = tmp_path / ".pythinker" / "agents" / "research"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}")

    result = runner.invoke(agents_app, ["delete", "research"])
    assert result.exit_code == 1
    assert "--confirm" in result.output
    assert target.exists()


def test_delete_with_wrong_confirm_is_refused(runner, tmp_path):
    target = tmp_path / ".pythinker" / "agents" / "research"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}")

    result = runner.invoke(agents_app, ["delete", "research", "--confirm", "coding"])
    assert result.exit_code == 1
    assert target.exists()


def test_delete_with_correct_confirm_succeeds(runner, tmp_path):
    target = tmp_path / ".pythinker" / "agents" / "research"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}")

    result = runner.invoke(agents_app, ["delete", "research", "--confirm", "research"])
    assert result.exit_code == 0, result.output
    assert not target.exists()


def test_delete_unknown_id_fails_clean(runner, tmp_path):
    result = runner.invoke(agents_app, ["delete", "ghost", "--confirm", "ghost"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


@pytest.mark.parametrize("bad_id", ["", "a/b", "..", ".", "x\\y"])
def test_invalid_ids_are_refused(runner, bad_id):
    """Path-separator chars and traversal segments are refused."""
    result = runner.invoke(agents_app, ["create", bad_id])
    assert result.exit_code == 1
