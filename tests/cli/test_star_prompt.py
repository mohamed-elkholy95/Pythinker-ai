"""Tests for the GitHub star prompt module.

The orchestrator takes every side-effecting helper as an injected kwarg,
so each test stubs only the deps relevant to that branch. No subprocess
ever runs against real `gh`, no network, no real ~/.pythinker dir.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime

import pytest

from pythinker.cli import star_prompt


@pytest.fixture(autouse=True)
def _isolated_config_path(tmp_path):
    """Redirect the config path via ``set_config_path`` to a per-test
    temp dir so star-prompt tests never read or write the real
    ``~/.pythinker``. Captures the prior value and restores it in a
    ``finally`` so the process-global ``loader._current_config_path``
    stays clean for later tests in the same pytest run. Mirrors the
    ``redirected_config`` pattern in ``tests/cli/test_backup_cleanup.py``.
    """
    from pythinker.config import loader

    cfg = tmp_path / "config.json"
    cfg.write_text("{}")

    previous = loader._current_config_path
    loader.set_config_path(cfg)
    try:
        yield
    finally:
        loader._current_config_path = previous


def test_state_path_lives_under_runtime_state_dir(tmp_path):
    """`_state_path()` must resolve under `get_runtime_subdir("state")`."""
    path = star_prompt._state_path()
    assert path == tmp_path / "state" / "star-prompt.json"
    assert path.parent.exists()  # get_runtime_subdir ensures parent dir


def test_has_been_prompted_false_when_state_missing(tmp_path):
    assert star_prompt._has_been_prompted() is False


def test_has_been_prompted_true_when_state_valid(tmp_path):
    state = tmp_path / "state" / "star-prompt.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"prompted_at": "2026-05-02T00:00:00Z"}))
    assert star_prompt._has_been_prompted() is True


def test_has_been_prompted_false_on_garbage_json(tmp_path):
    state = tmp_path / "state" / "star-prompt.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("not json at all {")
    assert star_prompt._has_been_prompted() is False


def test_has_been_prompted_false_when_field_missing(tmp_path):
    state = tmp_path / "state" / "star-prompt.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"unrelated": "field"}))
    assert star_prompt._has_been_prompted() is False


def test_mark_prompted_writes_iso_timestamp(tmp_path):
    star_prompt._mark_prompted()
    state = tmp_path / "state" / "star-prompt.json"
    assert state.exists()
    payload = json.loads(state.read_text())
    assert "prompted_at" in payload
    # Round-trip through fromisoformat to confirm valid ISO 8601
    datetime.fromisoformat(payload["prompted_at"].replace("Z", "+00:00"))


def test_is_gh_authenticated_true_when_exit_zero(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)
    assert star_prompt._is_gh_authenticated() is True


def test_is_gh_authenticated_false_when_exit_nonzero(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="not logged in")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)
    assert star_prompt._is_gh_authenticated() is False


def test_is_gh_authenticated_false_when_gh_missing(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("gh not on PATH")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)
    assert star_prompt._is_gh_authenticated() is False


def test_is_gh_authenticated_false_on_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=3)
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)
    assert star_prompt._is_gh_authenticated() is False


def test_star_repo_success(monkeypatch):
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)

    ok, err = star_prompt._star_repo()
    assert ok is True
    assert err == ""
    assert captured["cmd"] == ["gh", "api", "-X", "PUT", f"/user/starred/{star_prompt.REPO}"]
    assert captured["kwargs"]["timeout"] == star_prompt.GH_STAR_TIMEOUT_S


def test_star_repo_failure_includes_stderr(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="HTTP 401: bad credentials")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)

    ok, err = star_prompt._star_repo()
    assert ok is False
    assert "HTTP 401" in err


def test_star_repo_failure_falls_back_to_stdout(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=2, stdout="some output", stderr="")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)

    ok, err = star_prompt._star_repo()
    assert ok is False
    assert "some output" in err


def test_star_repo_failure_on_exception(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("gh disappeared mid-run")
    monkeypatch.setattr(star_prompt.subprocess, "run", fake_run)

    ok, err = star_prompt._star_repo()
    assert ok is False
    assert "gh disappeared" in err


@pytest.mark.parametrize("user_input,expected", [
    ("", True),
    ("y", True),
    ("Y", True),
    ("yes", True),
    ("YES", True),
    ("  y  ", True),
    ("n", False),
    ("no", False),
    ("nope", False),
    ("anything else", False),
])
def test_ask_yes_no_parsing(monkeypatch, user_input, expected):
    monkeypatch.setattr("builtins.input", lambda _prompt: user_input)
    assert star_prompt._ask_yes_no("?") is expected


def _full_stub_deps(**overrides):
    """Build a deps dict with safe defaults that all happy-path the call."""
    defaults = dict(
        stdin_is_tty=True,
        stdout_is_tty=True,
        has_been_prompted_fn=lambda: False,
        is_gh_authenticated_fn=lambda: True,
        mark_prompted_fn=lambda: None,
        ask_yes_no_fn=lambda _prompt: True,
        star_repo_fn=lambda: (True, ""),
        log_fn=lambda _msg: None,
        warn_fn=lambda _msg: None,
    )
    defaults.update(overrides)
    return defaults


def test_skip_when_stdin_not_tty():
    calls = []
    deps = _full_stub_deps(
        stdin_is_tty=False,
        has_been_prompted_fn=lambda: calls.append("checked") or False,
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert calls == []  # short-circuited before any other check


def test_skip_when_stdout_not_tty():
    calls = []
    deps = _full_stub_deps(
        stdout_is_tty=False,
        has_been_prompted_fn=lambda: calls.append("checked") or False,
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert calls == []


@pytest.mark.parametrize("env_value", ["1", "true", "TRUE", "yes", "Yes"])
def test_skip_when_env_opt_out_set(monkeypatch, env_value):
    monkeypatch.setenv(star_prompt.ENV_OPT_OUT, env_value)
    calls = []
    deps = _full_stub_deps(
        has_been_prompted_fn=lambda: calls.append("checked") or False,
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert calls == []


@pytest.mark.parametrize("env_value", ["", "0", "false", "no"])
def test_does_not_skip_on_falsy_env(monkeypatch, env_value):
    monkeypatch.setenv(star_prompt.ENV_OPT_OUT, env_value)
    asked = []
    deps = _full_stub_deps(ask_yes_no_fn=lambda _p: asked.append(True) or True)
    star_prompt.maybe_prompt_github_star(**deps)
    assert asked == [True]


def test_skip_when_already_prompted():
    calls = []
    deps = _full_stub_deps(
        has_been_prompted_fn=lambda: True,
        is_gh_authenticated_fn=lambda: calls.append("gh") or True,
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert calls == []


def test_skip_when_gh_not_authenticated_state_not_marked():
    marked = []
    asked = []
    deps = _full_stub_deps(
        is_gh_authenticated_fn=lambda: False,
        mark_prompted_fn=lambda: marked.append(True),
        ask_yes_no_fn=lambda _p: asked.append(True) or True,
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert marked == []  # critical: do NOT write state when gh missing
    assert asked == []


def test_marks_prompted_before_asking():
    """The single most important invariant: state is written first."""
    order = []
    deps = _full_stub_deps(
        mark_prompted_fn=lambda: order.append("mark"),
        ask_yes_no_fn=lambda _p: order.append("ask") or True,
        star_repo_fn=lambda: (True, ""),
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert order[0] == "mark"
    assert order[1] == "ask"


def test_user_accepts_and_star_succeeds_logs_thanks():
    logged = []
    deps = _full_stub_deps(
        ask_yes_no_fn=lambda _p: True,
        star_repo_fn=lambda: (True, ""),
        log_fn=lambda msg: logged.append(msg),
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert any("Thanks for the star" in m for m in logged)


def test_user_accepts_and_star_fails_warns_with_error():
    warned = []
    deps = _full_stub_deps(
        ask_yes_no_fn=lambda _p: True,
        star_repo_fn=lambda: (False, "boom"),
        warn_fn=lambda msg: warned.append(msg),
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert any("Could not star" in m and "boom" in m for m in warned)


def test_user_declines_does_not_call_star():
    starred = []
    deps = _full_stub_deps(
        ask_yes_no_fn=lambda _p: False,
        star_repo_fn=lambda: starred.append(True) or (True, ""),
    )
    star_prompt.maybe_prompt_github_star(**deps)
    assert starred == []


def test_no_env_var_set_does_not_skip(monkeypatch):
    monkeypatch.delenv(star_prompt.ENV_OPT_OUT, raising=False)
    asked = []
    deps = _full_stub_deps(ask_yes_no_fn=lambda _p: asked.append(True) or False)
    star_prompt.maybe_prompt_github_star(**deps)
    assert asked == [True]


def test_orchestrator_is_imported_in_commands_module():
    """`commands.py` must import `maybe_prompt_github_star` so the wiring fires."""
    from pythinker.cli import commands
    assert hasattr(commands, "maybe_prompt_github_star")
