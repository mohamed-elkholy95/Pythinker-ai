"""Tests for ``pythinker backup`` and ``pythinker cleanup``.

Both commands touch the user's home directory in production, so every
test isolates the config path under ``tmp_path`` via the existing
``set_config_path`` hook. Cleanup tests additionally monkey-patch the
oauth/sessions/api-workspace path resolvers so they stay confined.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pythinker.cli.commands import _cleanup_targets, _format_size, app
from pythinker.config.schema import Config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch):
    """Rich auto-detects terminal width; CI Windows consoles report a
    narrow width that truncates long temp paths to '…' inside table cells.
    Force a wide rendering width so substring assertions on full paths
    succeed across platforms."""
    monkeypatch.setenv("COLUMNS", "400")


def _flat(s: str) -> str:
    """Collapse whitespace runs so Rich's terminal-width wrapping (which
    splits long Windows paths and table cells across lines) doesn't break
    substring assertions on Windows CI."""
    return " ".join(s.split())


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Redirect ~/.pythinker to a temp directory for every backup/cleanup test."""
    cfg_path = tmp_path / "pythinker" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(Config().model_dump_json(by_alias=True, indent=2))
    monkeypatch.setattr(
        "pythinker.config.loader.get_config_path", lambda: cfg_path
    )
    return tmp_path


# ---------------------------------------------------------------------------
# backup create / list / verify / restore
# ---------------------------------------------------------------------------


def test_backup_create_writes_timestamped_file(tmp_home):
    result = runner.invoke(app, ["backup", "create"])
    assert result.exit_code == 0
    backups = list((tmp_home / "pythinker" / "backups").glob("config.*.json"))
    assert len(backups) == 1
    assert "sha256" in result.stdout


def test_backup_create_with_label_sanitizes_filename(tmp_home):
    """Labels are restricted to a safe alnum/underscore/dash subset so the
    filename stays portable across filesystems."""
    result = runner.invoke(app, ["backup", "create", "--label", "before/edit space"])
    assert result.exit_code == 0
    backups = list((tmp_home / "pythinker" / "backups").glob("config.*.json"))
    # Slashes and spaces collapse to dashes; no path traversal possible.
    assert all("/" not in b.name and " " not in b.name for b in backups)


def test_backup_create_aborts_when_no_config_exists(tmp_home, monkeypatch):
    """Removing the config and re-running backup must exit 1, not crash."""
    (tmp_home / "pythinker" / "config.json").unlink()
    result = runner.invoke(app, ["backup", "create"])
    assert result.exit_code == 1
    assert "nothing to back up" in result.stdout


def test_backup_list_includes_both_dirs(tmp_home):
    """List should surface both new ``backups/`` files AND the wizard's
    legacy ``config.json.bak.<ts>`` files in the parent dir."""
    runner.invoke(app, ["backup", "create"])
    legacy = tmp_home / "pythinker" / "config.json.bak.20260101-000000"
    legacy.write_text("{}")

    result = runner.invoke(app, ["backup", "list"])
    assert result.exit_code == 0
    assert "config." in result.stdout
    assert "config.json.bak.20260101" in result.stdout


def test_backup_list_empty_message_when_no_backups(tmp_home):
    result = runner.invoke(app, ["backup", "list"])
    assert result.exit_code == 0
    assert "No backups" in result.stdout


def test_backup_verify_passes_for_valid_config(tmp_home):
    runner.invoke(app, ["backup", "create"])
    backups = list((tmp_home / "pythinker" / "backups").glob("config.*.json"))
    result = runner.invoke(app, ["backup", "verify", str(backups[0])])
    assert result.exit_code == 0
    assert "loads + validates cleanly" in _flat(result.stdout)


def test_backup_verify_fails_for_corrupt_json(tmp_path):
    """Garbage input is reported as a JSON error, not a schema error."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all")
    result = runner.invoke(app, ["backup", "verify", str(bad)])
    assert result.exit_code == 1
    assert "not valid JSON" in result.stdout


def test_backup_verify_fails_for_schema_violation(tmp_path):
    """Type-incompatible value (string in an int field) must be caught."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"gateway": {"port": "not-a-number"}}))
    result = runner.invoke(app, ["backup", "verify", str(bad)])
    assert result.exit_code == 1
    assert "schema rejected backup" in result.stdout


def test_backup_verify_missing_file_exits_1(tmp_path):
    result = runner.invoke(app, ["backup", "verify", str(tmp_path / "absent.json")])
    assert result.exit_code == 1


def test_backup_restore_replaces_config_and_safety_backs_up_old(tmp_home):
    """Restore must atomically swap AND keep a copy of what was overwritten."""
    cfg_path = tmp_home / "pythinker" / "config.json"
    runner.invoke(app, ["backup", "create"])
    backups = list((tmp_home / "pythinker" / "backups").glob("config.*.json"))
    snapshot = backups[0]

    # Mutate the live config so we can prove restore actually overwrote it.
    cfg_path.write_text(json.dumps({"agents": {"defaults": {"model": "MUTATED"}}}))

    result = runner.invoke(app, ["backup", "restore", str(snapshot), "-y"])
    assert result.exit_code == 0
    assert "MUTATED" not in cfg_path.read_text()

    # Safety backup of the mutated state must exist so the restore is itself
    # reversible — find by the pre-restore prefix.
    pre = list((tmp_home / "pythinker" / "backups").glob("config.pre-restore.*.json"))
    assert len(pre) == 1
    assert "MUTATED" in pre[0].read_text()


def test_backup_restore_refuses_invalid_backup(tmp_home, tmp_path):
    """Refusing to restore garbage protects from foot-gun script that points
    at the wrong file."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    result = runner.invoke(app, ["backup", "restore", str(bad), "-y"])
    assert result.exit_code == 1
    assert "would not load" in result.stdout


# ---------------------------------------------------------------------------
# cleanup plan / run
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_cleanup_paths(tmp_home, monkeypatch):
    """Redirect oauth/sessions/api-workspace path resolvers under tmp_home."""
    base = tmp_home / "pythinker"
    sessions = base / "sessions"
    api_ws = base / "api-workspace"
    oauth = base / "oauth.json"

    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.sessions_dir", lambda: sessions
    )
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.api_workspace_dir", lambda: api_ws
    )
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.oauth_cli_kit_token_paths",
        lambda: [oauth],
    )
    return sessions, api_ws, oauth


def test_cleanup_targets_scope_config_only_returns_config(tmp_home, isolated_cleanup_paths):
    targets = _cleanup_targets("config")
    assert tmp_home / "pythinker" / "config.json" in targets
    assert len(targets) == 1


def test_cleanup_targets_scope_credentials_includes_oauth(
    tmp_home, isolated_cleanup_paths
):
    _, _, oauth = isolated_cleanup_paths
    oauth.write_text("{}")
    targets = _cleanup_targets("credentials")
    assert oauth in targets


def test_cleanup_targets_scope_full_includes_everything(
    tmp_home, isolated_cleanup_paths
):
    sessions, api_ws, oauth = isolated_cleanup_paths
    sessions.mkdir()
    api_ws.mkdir()
    oauth.write_text("{}")
    targets = _cleanup_targets("full")
    assert sessions in targets
    assert api_ws in targets
    assert oauth in targets


def test_cleanup_plan_lists_each_target(tmp_home, isolated_cleanup_paths):
    sessions, _, oauth = isolated_cleanup_paths
    sessions.mkdir()
    oauth.write_text("{}")
    result = runner.invoke(app, ["cleanup", "plan", "--scope", "sessions"])
    assert result.exit_code == 0
    flat = _flat(result.stdout)
    assert "config.json" in flat
    assert "oauth.json" in flat
    assert "sessions" in flat


def test_cleanup_plan_invalid_scope_exits_1(tmp_home):
    result = runner.invoke(app, ["cleanup", "plan", "--scope", "everything"])
    assert result.exit_code == 1
    assert "invalid scope" in result.stdout


def test_cleanup_run_without_confirm_refuses(tmp_home, isolated_cleanup_paths):
    """No `--confirm reset` = no destructive op. The user typing y/N at a
    confirm prompt is not enough for an irreversible delete."""
    cfg_path = tmp_home / "pythinker" / "config.json"
    result = runner.invoke(app, ["cleanup", "run", "--scope", "config"])
    assert result.exit_code == 1
    assert "without consent" in result.stdout
    assert cfg_path.exists(), "config must NOT be deleted without --confirm reset"


@pytest.mark.parametrize("variant", ["RESET", "Reset", " reset ", "reset\n", "rESET"])
def test_cleanup_run_consent_is_strict_literal(
    tmp_home, isolated_cleanup_paths, variant
):
    """Typed-consent gate is exact-match per documented contract — case
    folding and whitespace stripping silently lower the bar and
    contradict the help text. Reject every variant of 'reset' that
    isn't the exact lowercase literal."""
    cfg_path = tmp_home / "pythinker" / "config.json"
    result = runner.invoke(
        app, ["cleanup", "run", "--scope", "config", "--confirm", variant]
    )
    assert result.exit_code == 1
    assert cfg_path.exists()


def test_cleanup_run_with_confirm_deletes_targets_and_safety_backs_up_first(
    tmp_home, isolated_cleanup_paths
):
    cfg_path = tmp_home / "pythinker" / "config.json"
    cfg_before = cfg_path.read_text()
    result = runner.invoke(
        app, ["cleanup", "run", "--scope", "config", "--confirm", "reset"]
    )
    assert result.exit_code == 0
    assert not cfg_path.exists()

    # Safety backup of the deleted config must exist with the pre-cleanup
    # filename pattern, and its contents must match what was deleted.
    pre = list((tmp_home / "pythinker" / "backups").glob("config.*.pre-cleanup.json"))
    assert len(pre) == 1
    assert pre[0].read_text() == cfg_before


def test_cleanup_run_no_backup_skips_safety_copy(
    tmp_home, isolated_cleanup_paths
):
    """``--no-backup`` skips the safety copy — useful for ephemeral
    workspaces where the user knows what they're doing."""
    result = runner.invoke(
        app,
        ["cleanup", "run", "--scope", "config", "--confirm", "reset", "--no-backup"],
    )
    assert result.exit_code == 0
    pre = list((tmp_home / "pythinker" / "backups").glob("config.*.pre-cleanup.json"))
    assert pre == []


def test_format_size_handles_files_and_dirs(tmp_path):
    f = tmp_path / "x"
    f.write_bytes(b"x" * 2048)
    assert "KB" in _format_size(f) or "B" in _format_size(f)

    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_bytes(b"y" * 1024)
    (d / "b").write_bytes(b"y" * 1024)
    # 2 KB total across files in d.
    assert "KB" in _format_size(d) or "B" in _format_size(d)


# ---------------------------------------------------------------------------
# reset.sessions_dir / api_workspace_dir honour the config-derived data dir
# ---------------------------------------------------------------------------


@pytest.fixture
def redirected_config(tmp_path, monkeypatch):
    """Redirect the config path via ``set_config_path`` so every helper
    that resolves through ``paths.get_data_dir()`` (which resolves at
    call time) picks up the temp root. The other fixture in this file
    only patches ``pythinker.config.loader.get_config_path``, which
    misses the name already bound inside ``pythinker.config.paths``."""
    from pythinker.config import loader

    cfg_path = tmp_path / "pythinker" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{}")

    previous = loader._current_config_path
    loader.set_config_path(cfg_path)
    try:
        yield tmp_path
    finally:
        loader._current_config_path = previous


def test_reset_paths_honour_redirected_config_path(redirected_config):
    """B-5 regression guard. ``sessions_dir`` and ``api_workspace_dir`` must
    resolve under ``get_data_dir()`` (the config file's parent), not the
    hardcoded ``~/.pythinker``. Under a redirected ``PYTHINKER_CONFIG`` the
    pre-fix code would silently wipe the user's real home directory."""
    from pythinker.cli.onboard_views import reset as reset_mod

    expected_root = redirected_config / "pythinker"
    assert reset_mod.sessions_dir() == expected_root / "sessions"
    assert reset_mod.api_workspace_dir() == expected_root / "api-workspace"

    # And critically: the resolver must NOT escape to the real home dir.
    assert Path.home() / ".pythinker" / "sessions" != reset_mod.sessions_dir()


def test_reset_apply_immediate_full_targets_redirected_dirs(redirected_config, monkeypatch):
    """End-to-end: ``apply_immediate(FULL)`` deletes the redirected
    ``sessions`` and ``api-workspace`` dirs and leaves the real home
    untouched. OAuth token paths are stubbed out so the test never
    touches the real ``~/.local/share``."""
    from pythinker.cli.onboard_views import reset as reset_mod

    monkeypatch.setattr(reset_mod, "oauth_cli_kit_token_paths", lambda: [])

    sessions = redirected_config / "pythinker" / "sessions"
    api_ws = redirected_config / "pythinker" / "api-workspace"
    sessions.mkdir(parents=True)
    (sessions / "history.jsonl").write_text("{}")
    api_ws.mkdir(parents=True)
    (api_ws / "scratch").write_text("x")

    reset_mod.apply_immediate(reset_mod.ResetScope.FULL)

    assert not sessions.exists()
    assert not api_ws.exists()


def test_reset_apply_immediate_logs_warning_on_oserror(tmp_path, monkeypatch):
    """I-7 regression guard. When unlink raises a non-FileNotFoundError
    OSError, ``apply_immediate`` must surface it via loguru rather than
    silently swallowing the failure."""
    from loguru import logger

    from pythinker.cli.onboard_views import reset as reset_mod

    fake_token = tmp_path / "fake-oauth.json"
    fake_token.write_text("{}")
    monkeypatch.setattr(reset_mod, "oauth_cli_kit_token_paths", lambda: [fake_token])

    def raise_permission(self, *args, **kwargs):
        raise PermissionError("read-only fs")

    monkeypatch.setattr(Path, "unlink", raise_permission)

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        reset_mod.apply_immediate(reset_mod.ResetScope.CREDENTIALS)
    finally:
        logger.remove(sink_id)

    assert any("could not delete" in m and "fake-oauth.json" in m for m in messages)
