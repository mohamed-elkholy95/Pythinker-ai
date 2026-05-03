"""Tests for the linear onboarding orchestrator."""

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from pythinker.cli.onboard import (
    OnboardResult,
    StepResult,
    _run_linear_wizard,
    _WizardContext,
)
from pythinker.cli.onboard_views import clack as _clack
from pythinker.config.schema import Config


def test_orchestrator_runs_steps_in_order(monkeypatch):
    """Steps execute in sequence, left to right."""
    log = []

    def fake_step_a(ctx):
        log.append("a")
        return StepResult(status="continue")

    def fake_step_b(ctx):
        log.append("b")
        return StepResult(status="continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [fake_step_a, fake_step_b],
    )

    ctx = _WizardContext(draft=Config())
    result = _run_linear_wizard(ctx)
    assert log == ["a", "b"]
    assert isinstance(result, OnboardResult)


def test_orchestrator_short_circuits_on_abort(monkeypatch):
    """When a step aborts, orchestrator stops and returns should_save=False."""
    log = []

    def fake_step_a(ctx):
        log.append("a")
        return StepResult(status="abort", message="user said no")

    def fake_step_b(ctx):
        log.append("b")
        return StepResult(status="continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [fake_step_a, fake_step_b],
    )

    ctx = _WizardContext(draft=Config())
    result = _run_linear_wizard(ctx)
    assert log == ["a"]
    assert result.should_save is False


def test_orchestrator_skips_step_marked_skip(monkeypatch):
    """When a step returns skip, it is not logged but execution continues."""
    log = []

    def fake_step_a(ctx):
        log.append("a")
        return StepResult(status="skip")

    def fake_step_b(ctx):
        log.append("b")
        return StepResult(status="continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [fake_step_a, fake_step_b],
    )

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    assert log == ["a", "b"]


def test_orchestrator_catches_wizard_cancelled(monkeypatch):
    """WizardCancelled exception returns should_save=False."""
    from pythinker.cli.onboard_views.clack import WizardCancelled

    def fake_step_a(ctx):
        raise WizardCancelled("user pressed Ctrl-C")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [fake_step_a],
    )

    ctx = _WizardContext(draft=Config())
    result = _run_linear_wizard(ctx)
    assert result.should_save is False


def test_orchestrator_catches_step_exception(monkeypatch):
    """Any other exception is caught, logged, and returns should_save=False."""

    def fake_step_a(ctx):
        raise RuntimeError("step blew up")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [fake_step_a],
    )

    ctx = _WizardContext(draft=Config())
    result = _run_linear_wizard(ctx)
    assert result.should_save is False


def test_orchestrator_drains_deferred_callbacks_after_successful_run(monkeypatch):
    """Steps can register ``ctx.register_deferred(fn)`` callbacks that run
    once the orchestrator finishes successfully. Mirrors pythinker's
    ``WizardSession.deferred``."""
    fired: list[str] = []

    def step_a(ctx):
        ctx.register_deferred(lambda: fired.append("after-a"))
        return StepResult(status="continue")

    def step_b(ctx):
        ctx.register_deferred(lambda: fired.append("after-b"))
        return StepResult(status="continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [step_a, step_b],
    )

    ctx = _WizardContext(draft=Config())
    result = _run_linear_wizard(ctx)
    # Registration order is preserved.
    assert fired == ["after-a", "after-b"]
    assert result.should_save is True


def test_orchestrator_skips_deferred_callbacks_on_abort(monkeypatch):
    """A step that aborts must not trigger any deferreds previously
    registered — the run did not complete successfully."""
    fired: list[str] = []

    def step_a(ctx):
        ctx.register_deferred(lambda: fired.append("after-a"))
        return StepResult(status="continue")

    def step_b(ctx):
        return StepResult(status="abort", message="nope")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [step_a, step_b],
    )

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    assert fired == []


def test_orchestrator_isolates_deferred_callback_failures(monkeypatch):
    """One deferred raising must not block the others — the orchestrator
    logs the failure and moves on."""
    fired: list[str] = []

    def step(ctx):
        ctx.register_deferred(lambda: fired.append("first"))
        ctx.register_deferred(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        ctx.register_deferred(lambda: fired.append("third"))
        return StepResult(status="continue")

    monkeypatch.setattr("pythinker.cli.onboard._WIZARD_STEPS", [step])

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    assert fired == ["first", "third"]


def test_orchestrator_back_at_first_step_is_noop_then_advances(monkeypatch):
    """Step 0 has no history to pop. ``back`` from there must not crash and
    must not advance — the user re-enters the same step. We use a counter
    so the second visit returns ``continue`` and the wizard moves on."""
    visits = {"a": 0, "b": 0}

    def step_a(ctx):
        visits["a"] += 1
        return StepResult(status="back" if visits["a"] == 1 else "continue")

    def step_b(ctx):
        visits["b"] += 1
        return StepResult(status="continue")

    monkeypatch.setattr("pythinker.cli.onboard._WIZARD_STEPS", [step_a, step_b])

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    assert visits == {"a": 2, "b": 1}


def test_orchestrator_back_pops_history_and_re_runs_previous_step(monkeypatch):
    """Step B emitting ``back`` returns control to step A, which on its
    second visit returns ``continue`` and the wizard advances past B again
    (this time B returns ``continue`` so the run completes)."""
    log: list[str] = []
    visits = {"a": 0, "b": 0}

    def step_a(ctx):
        visits["a"] += 1
        log.append(f"a{visits['a']}")
        return StepResult(status="continue")

    def step_b(ctx):
        visits["b"] += 1
        log.append(f"b{visits['b']}")
        return StepResult(status="back" if visits["b"] == 1 else "continue")

    monkeypatch.setattr("pythinker.cli.onboard._WIZARD_STEPS", [step_a, step_b])

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    # Sequence: a → b (back) → a → b → done.
    assert log == ["a1", "b1", "a2", "b2"]


def test_orchestrator_back_skips_over_skipped_steps(monkeypatch):
    """Steps that returned ``skip`` are not pushed to the history stack —
    so ``back`` from step C lands on A (the last step that ran), not B."""
    log: list[str] = []
    visits = {"a": 0, "b": 0, "c": 0}

    def step_a(ctx):
        visits["a"] += 1
        log.append(f"a{visits['a']}")
        return StepResult(status="continue")

    def step_b(ctx):
        visits["b"] += 1
        log.append(f"b{visits['b']}")
        return StepResult(status="skip")

    def step_c(ctx):
        visits["c"] += 1
        log.append(f"c{visits['c']}")
        return StepResult(status="back" if visits["c"] == 1 else "continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [step_a, step_b, step_c],
    )

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    # b is skipped; back from c lands on a; from a we re-walk b (still skip)
    # then c (this time continue).
    assert log == ["a1", "b1", "c1", "a2", "b2", "c2"]


def test_orchestrator_next_step_jumps_and_records_history(monkeypatch):
    """``StepResult(next_step="step_c")`` jumps directly to step C, but
    pushes the current step onto history so a later ``back`` returns here.
    Used by skip-ahead flows and the security-disclaimer / use-existing
    branch in production."""
    log: list[str] = []

    def step_a(ctx):
        log.append("a")
        return StepResult(status="continue", next_step="step_c")

    def step_b(ctx):
        log.append("b")
        return StepResult(status="continue")

    def step_c(ctx):
        log.append("c")
        return StepResult(status="continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [step_a, step_b, step_c],
    )

    ctx = _WizardContext(draft=Config())
    _run_linear_wizard(ctx)
    # a → c (jumped past b). Then no history-pop happens, walker exits at end.
    assert log == ["a", "c"]


def test_step_provider_picker_back_choice_returns_back_status():
    """Selecting [Back] in the provider picker yields a back StepResult so
    the orchestrator can pop history. The wizard now offers cross-step
    back-navigation (Phase 1 task 5)."""
    from pythinker.cli.onboard import _step_provider_picker

    ctx = _WizardContext(draft=Config())
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="__back__",
    ):
        result = _step_provider_picker(ctx)
    assert result.status == "back"
    assert ctx.auth is None  # Selection was discarded.


def test_step_default_model_back_choice_returns_back_status():
    """[Back] from the default-model picker returns ``back`` so the user
    can change provider after seeing the model list."""
    from pythinker.cli.onboard import _step_default_model

    ctx = _WizardContext(draft=Config(), auth="openai_codex")
    ctx.draft.agents.defaults.model = "openai-codex/gpt-5.5-mini"
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="__back__",
    ):
        result = _step_default_model(ctx)
    assert result.status == "back"


# ---------------------------------------------------------------------------
# Phase 1 task 7 — post-save health check
# ---------------------------------------------------------------------------


def test_check_gateway_port_free_returns_ok_for_unbound_port():
    """When nothing is bound on (host, port) the helper returns ('ok', detail)."""
    import socket

    from pythinker.cli.onboard import _check_gateway_port_free

    # Pick an ephemeral free port.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()

    status, detail = _check_gateway_port_free("127.0.0.1", free_port)
    assert status == "ok"
    assert "free" in detail


def test_check_gateway_port_free_reports_warn_when_in_use():
    """When the port is held by another process the helper returns ``warn``
    with an 'in use' detail — health step then renders a yellow ⚠."""
    import socket

    from pythinker.cli.onboard import _check_gateway_port_free

    squatter = socket.socket()
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(1)
    port = squatter.getsockname()[1]
    try:
        status, detail = _check_gateway_port_free("127.0.0.1", port)
        assert status == "warn"
        # POSIX surfaces 'in use' / EADDRINUSE; Windows reports the same
        # bound-socket condition as 'permission denied' (WSAEACCES).
        assert any(s in detail.lower() for s in ("in use", "permission denied"))
    finally:
        squatter.close()


def test_step_post_save_health_skipped_on_use_existing():
    """When the user picked 'Use existing' (no save), the health step is
    skipped — there's nothing new to verify."""
    from pythinker.cli.onboard import _step_post_save_health

    ctx = _WizardContext(draft=Config(), use_existing=True)
    result = _step_post_save_health(ctx)
    assert result.status == "skip"


def test_step_post_save_health_renders_check_lines(monkeypatch):
    """The step emits one ``print_status`` line per check, prefixed with a
    status glyph (✓ / ⚠ / ✗)."""
    from pythinker.cli.doctor import CheckResult
    from pythinker.cli.onboard import _step_post_save_health

    monkeypatch.setattr(
        "pythinker.cli.doctor._check_workspace",
        lambda: CheckResult("ok", "Workspace", "/tmp/ws"),
    )
    monkeypatch.setattr(
        "pythinker.cli.doctor._check_default_model",
        lambda: CheckResult("ok", "Default model", "openai-codex/gpt-5"),
    )
    monkeypatch.setattr(
        "pythinker.cli.doctor._check_default_provider_auth",
        lambda: [CheckResult("warn", "Provider auth", "no token", fix="run pythinker login")],
    )

    ctx = _WizardContext(draft=Config())
    statuses: list[str] = []
    with patch(
        "pythinker.cli.onboard_views.clack.print_status",
        side_effect=statuses.append,
    ), patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_post_save_health(ctx)

    assert result.status == "continue"
    # Header line + one per check + the warn fix-hint line + the gateway port line.
    joined = "\n".join(statuses)
    assert "Health check:" in joined
    assert "✓ Workspace" in joined
    assert "✓ Default model" in joined
    assert "⚠ Provider auth" in joined
    assert "Fix: run pythinker login" in joined
    assert "Gateway port" in joined


# ---------------------------------------------------------------------------
# Phase 1 task 8 — required_headless_flags helper
# ---------------------------------------------------------------------------


def test_required_headless_flags_fresh_config_demands_provider_and_security_ack():
    """No prior config → caller needs at minimum: --non-interactive,
    --yes-security, an --auth choice, and a gateway-start decision."""
    from pythinker.cli.onboard import required_headless_flags

    flags = required_headless_flags(None)
    assert "--non-interactive" in flags
    assert "--yes-security" in flags
    assert any("--auth" in f for f in flags)
    assert "--skip-gateway" in flags


def test_required_headless_flags_with_authed_provider_omits_auth_flag():
    """When the config already has a credential for any provider, --auth is
    no longer needed (the wizard's auth step will pick up the existing key)."""
    from pythinker.cli.onboard import required_headless_flags

    cfg = Config()
    cfg.providers.anthropic.api_key = "sk-ant-fake-test-key"
    flags = required_headless_flags(cfg)
    assert "--non-interactive" in flags
    assert "--yes-security" in flags
    assert not any("--auth" in f for f in flags), (
        f"expected no --auth in flags but got: {flags}"
    )


def test_required_headless_flags_oauth_token_satisfies_auth_requirement():
    """OAuth providers count as authed when ``credential_source`` reports
    'oauth' — i.e. a token file exists on disk. Without that the helper
    treats them as unauthenticated (no api_key present)."""
    from pythinker.cli.onboard import required_headless_flags

    cfg = Config()
    # Without patching credential_source, OAuth providers report 'none' on a
    # fresh box → the helper should still demand --auth.
    flags = required_headless_flags(cfg)
    assert any("--auth" in f for f in flags)

    with patch("pythinker.auth.credential_source", return_value="oauth"):
        flags = required_headless_flags(cfg)
    assert not any("--auth" in f for f in flags)


# ---------------------------------------------------------------------------
# Phase 1 task 10 — per-step docs link footer
# ---------------------------------------------------------------------------


def test_emit_docs_link_prints_url_when_key_known():
    """``_emit_docs_link("provider")`` prints a one-line ``Docs: <url>``
    footer via clack.print_status. Mirrors pythinker's docs breadcrumb."""
    from pythinker.cli.onboard import _DOCS_LINKS, _emit_docs_link

    statuses: list[str] = []
    with patch(
        "pythinker.cli.onboard_views.clack.print_status",
        side_effect=statuses.append,
    ):
        _emit_docs_link("provider")

    assert any("Docs:" in s and _DOCS_LINKS["provider"] in s for s in statuses)


def test_emit_docs_link_silent_for_unknown_key():
    """Unknown keys must no-op silently — failing closed for an accessory."""
    from pythinker.cli.onboard import _emit_docs_link

    with patch("pythinker.cli.onboard_views.clack.print_status") as mock_status:
        _emit_docs_link("not-a-real-key")
    mock_status.assert_not_called()


def test_step_provider_picker_emits_docs_footer_on_success():
    """The provider picker prints a Docs: line right before returning,
    so the user sees the matching docs URL in the wizard transcript."""
    from pythinker.cli.onboard import _step_provider_picker

    ctx = _WizardContext(draft=Config())
    statuses: list[str] = []
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="anthropic",
    ), patch(
        "pythinker.cli.onboard_views.clack.print_status",
        side_effect=statuses.append,
    ):
        result = _step_provider_picker(ctx)

    assert result.status == "continue"
    joined = "\n".join(statuses)
    assert "Docs:" in joined
    assert "providers" in joined.lower()


def test_step_run_auth_oauth_failure_renders_actionable_panel():
    """When _login_via_oauth_remote raises, the wizard surfaces a What/Why/
    How panel before aborting — no bare traceback (Phase 1 task 9)."""
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
    )
    captured: dict = {}

    def _capture(*, what, why, how):
        captured["what"] = what
        captured["why"] = why
        captured["how"] = how

    with patch(
        "pythinker.auth.credential_source",
        return_value="none",
    ), patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
        side_effect=RuntimeError("network unreachable"),
    ), patch(
        "pythinker.cli.onboard_views.errors.render_actionable",
        side_effect=_capture,
    ):
        result = _step_run_auth(ctx)

    assert result.status == "abort"
    assert "what" in captured
    assert "browser-login" in captured["what"].lower() or "openai" in captured["what"].lower()
    assert "network unreachable" in captured["what"]
    assert captured["how"]
    assert captured["why"]


def test_step_summary_save_io_failure_renders_actionable_panel(tmp_path, monkeypatch):
    """OSError from save_config triggers an actionable panel + abort, not a
    bare traceback."""
    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)

    def _boom(_cfg, _path):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("pythinker.cli.onboard.save_config", _boom)

    captured: dict = {}

    def _capture(*, what, why, how):
        captured.update(what=what, why=why, how=how)

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    with patch(
        "pythinker.cli.onboard_views.errors.render_actionable",
        side_effect=_capture,
    ), patch(
        "pythinker.cli.onboard_views.summary.render_pre_save"
    ), patch(
        "pythinker.cli.onboard_views.clack.print_status"
    ), patch(
        "pythinker.cli.onboard_views.clack.bar_break"
    ):
        result = _step_summary_confirm(ctx)

    assert result.status == "abort"
    assert "Permission denied" in captured["what"] or "13" in captured["what"]
    assert "writable" in captured["how"].lower() or "permissions" in captured["how"].lower()


def test_print_required_flags_cli_short_circuits_with_zero_exit():
    """``pythinker onboard --print-required-flags`` exits 0 after writing the
    flag set to stdout. No wizard run, no save attempt."""
    from typer.testing import CliRunner

    from pythinker.cli.commands import app

    runner = CliRunner()
    with patch(
        "pythinker.cli.onboard.required_headless_flags",
        return_value=["--non-interactive", "--yes-security", "--skip-gateway"],
    ):
        result = runner.invoke(app, ["onboard", "--print-required-flags"])

    assert result.exit_code == 0, result.stdout
    assert "--non-interactive" in result.stdout
    assert "--yes-security" in result.stdout


def test_step_post_save_health_isolates_check_failures(monkeypatch):
    """If an individual doctor check raises, the step still emits the rest —
    the wizard cannot fail on a diagnostic accessory."""
    from pythinker.cli.onboard import _step_post_save_health

    def _explode():
        raise RuntimeError("doctor module exploded")

    monkeypatch.setattr("pythinker.cli.doctor._check_workspace", _explode)
    monkeypatch.setattr(
        "pythinker.cli.doctor._check_default_model",
        lambda: __import__("pythinker.cli.doctor", fromlist=["CheckResult"]).CheckResult(
            "ok", "Default model", "ok-model"
        ),
    )
    monkeypatch.setattr(
        "pythinker.cli.doctor._check_default_provider_auth",
        lambda: [],
    )

    ctx = _WizardContext(draft=Config())
    statuses: list[str] = []
    with patch(
        "pythinker.cli.onboard_views.clack.print_status",
        side_effect=statuses.append,
    ), patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_post_save_health(ctx)

    assert result.status == "continue"
    joined = "\n".join(statuses)
    assert "Workspace" in joined  # warn line emitted
    assert "ok-model" in joined  # later checks still ran


def test_orchestrator_use_existing_short_circuits_save(monkeypatch):
    """When use_existing is True, should_save is False despite completing all steps."""
    log = []

    def fake_step_a(ctx):
        log.append("a")
        return StepResult(status="continue")

    def fake_step_b(ctx):
        log.append("b")
        return StepResult(status="continue")

    monkeypatch.setattr(
        "pythinker.cli.onboard._WIZARD_STEPS",
        [fake_step_a, fake_step_b],
    )

    ctx = _WizardContext(draft=Config(), use_existing=True)
    result = _run_linear_wizard(ctx)
    assert log == ["a", "b"]
    assert result.should_save is False


def _capture_step(step_fn, ctx):
    buf = StringIO()
    with patch.object(_clack, "_OUT", buf):
        step_fn(ctx)
    return buf.getvalue()


def test_step_banner_prints_pythinker_brand(capsys):
    from pythinker.cli.onboard import _step_banner

    ctx = _WizardContext(draft=Config())
    _step_banner(ctx)
    out = capsys.readouterr().out
    assert "Pythinker" in out


def test_step_intro_opens_bar():
    from pythinker.cli.onboard import _step_intro

    ctx = _WizardContext(draft=Config())
    out = _capture_step(_step_intro, ctx)
    assert out.startswith("┌  Pythinker setup")


def test_step_outro_closes_bar():
    from pythinker.cli.onboard import _step_outro

    ctx = _WizardContext(draft=Config())
    out = _capture_step(_step_outro, ctx)
    assert out.startswith("└  ")
    assert "Pythinker is ready" in out


def test_step_security_yes(monkeypatch):
    """Picking 'Yes' from the select-style confirm continues the wizard.
    The disclaimer step now renders Yes/No as clack option dots rather
    than questionary's bare ``(y/N)`` widget."""
    from pythinker.cli.onboard import _step_security_disclaimer

    ctx = _WizardContext(draft=Config(), yes_security=False)
    with patch("pythinker.cli.onboard_views.risk_ack.clack.select", return_value="yes"), \
         patch("pythinker.cli.onboard_views.risk_ack.clack.note"):
        result = _step_security_disclaimer(ctx)
    assert result.status == "continue"


def test_step_security_no_aborts(monkeypatch):
    from pythinker.cli.onboard import _step_security_disclaimer

    ctx = _WizardContext(draft=Config(), yes_security=False)
    with patch("pythinker.cli.onboard_views.risk_ack.clack.select", return_value="no"), \
         patch("pythinker.cli.onboard_views.risk_ack.clack.note"):
        result = _step_security_disclaimer(ctx)
    assert result.status == "abort"


def test_step_security_yes_security_flag_skips_confirm():
    from pythinker.cli.onboard import _step_security_disclaimer

    ctx = _WizardContext(draft=Config(), yes_security=True)
    with patch("pythinker.cli.onboard_views.risk_ack.clack.select") as confirm, \
         patch("pythinker.cli.onboard_views.risk_ack.clack.note"):
        result = _step_security_disclaimer(ctx)
    assert result.status == "continue"
    confirm.assert_not_called()


def test_step_security_non_interactive_without_yes_security_exits(capsys):
    import pytest

    from pythinker.cli.onboard import _step_security_disclaimer

    ctx = _WizardContext(draft=Config(), non_interactive=True, yes_security=False)
    with patch("pythinker.cli.onboard_views.risk_ack.clack.note"):
        with pytest.raises(SystemExit) as exc:
            _step_security_disclaimer(ctx)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--yes-security" in err


def test_step_security_skipped_on_use_existing():
    from pythinker.cli.onboard import _step_security_disclaimer

    ctx = _WizardContext(draft=Config(), use_existing=True)
    result = _step_security_disclaimer(ctx)
    assert result.status == "skip"


def test_step_existing_config_no_file_skips(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_existing_config

    monkeypatch.setattr(
        "pythinker.cli.onboard.get_config_path",
        lambda: tmp_path / "missing.json",
    )

    ctx = _WizardContext(draft=Config())
    result = _step_existing_config(ctx)
    assert result.status == "skip"


def test_step_existing_config_use_existing_short_circuits(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_existing_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"version": 1}')

    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr(
        "pythinker.cli.onboard.load_config",
        lambda *a, **kw: Config(),
    )

    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="use-existing",
    ), patch("pythinker.cli.onboard_views.summary.render_existing_summary"):
        ctx = _WizardContext(draft=Config())
        result = _step_existing_config(ctx)

    assert result.status == "continue"
    assert ctx.use_existing is True


def test_use_existing_selection_does_not_prompt_later_steps(tmp_path, monkeypatch):
    """Picking Use existing should not continue into provider/channel/search/save prompts."""
    import json

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"version": 1}))

    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr("pythinker.cli.onboard.load_config", lambda *a, **kw: Config())

    def select_once(question, **_kwargs):
        if question == "What would you like to do?":
            return "use-existing"
        raise AssertionError(f"unexpected prompt after Use existing: {question}")

    ctx = _WizardContext(draft=Config(), yes_security=True, skip_gateway=False)
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=select_once,
    ), patch("pythinker.cli.onboard_views.summary.render_existing_summary"), \
         patch("pythinker.cli.onboard_views.clack.print_status"), \
         patch("pythinker.cli.onboard_views.clack.bar_break"), \
         patch(
             "pythinker.cli.onboard_views.clack.abort",
             side_effect=AssertionError("wizard aborted after Use existing"),
         ):
        result = _run_linear_wizard(ctx)

    assert result.should_save is False


def test_step_existing_config_reset_credentials_deletes_immediately(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_existing_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"version": 1}')
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "abc").write_text("data")

    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr("pythinker.cli.onboard.load_config", lambda *a, **kw: Config())
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.sessions_dir", lambda: sessions_dir
    )
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.oauth_cli_kit_token_paths",
        lambda: [],
    )

    selects = iter(["reset", "credentials"])
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch("pythinker.cli.onboard_views.clack.text", return_value="reset"), \
       patch("pythinker.cli.onboard_views.summary.render_existing_summary"):
        ctx = _WizardContext(draft=Config())
        result = _step_existing_config(ctx)

    assert result.status == "continue"
    assert ctx.reset_pending is True
    # Sessions are NOT deleted at "credentials" scope (only at >= sessions).
    assert sessions_dir.exists()


def test_step_existing_config_reset_sessions_deletes_sessions(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_existing_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"version": 1}')
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "abc").write_text("data")

    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr("pythinker.cli.onboard.load_config", lambda *a, **kw: Config())
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.sessions_dir", lambda: sessions_dir
    )
    monkeypatch.setattr(
        "pythinker.cli.onboard_views.reset.oauth_cli_kit_token_paths", lambda: []
    )

    selects = iter(["reset", "sessions"])
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch("pythinker.cli.onboard_views.clack.text", return_value="reset"), \
       patch("pythinker.cli.onboard_views.summary.render_existing_summary"):
        ctx = _WizardContext(draft=Config())
        _step_existing_config(ctx)

    assert not sessions_dir.exists()


def test_step_existing_config_reset_typed_confirm_required(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_existing_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"version": 1}')

    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr("pythinker.cli.onboard.load_config", lambda *a, **kw: Config())

    selects = iter(["reset", "config"])
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch(
        "pythinker.cli.onboard_views.clack.text", return_value="not-the-word"
    ), patch("pythinker.cli.onboard_views.summary.render_existing_summary"):
        ctx = _WizardContext(draft=Config())
        result = _step_existing_config(ctx)
    assert result.status == "abort"


def test_step_flow_picker_interactive():
    from pythinker.cli.onboard import _step_flow_picker

    ctx = _WizardContext(draft=Config(), use_existing=False)
    with patch("pythinker.cli.onboard_views.clack.select", return_value="manual"):
        result = _step_flow_picker(ctx)

    assert result.status == "continue"
    assert ctx.flow == "manual"


def test_step_flow_picker_flag_overrides():
    from pythinker.cli.onboard import _step_flow_picker

    ctx = _WizardContext(draft=Config(), flow="quickstart")
    with patch("pythinker.cli.onboard_views.clack.select") as sel:
        _step_flow_picker(ctx)

    assert ctx.flow == "quickstart"
    sel.assert_not_called()


def test_step_flow_picker_skipped_on_use_existing():
    from pythinker.cli.onboard import _step_flow_picker

    ctx = _WizardContext(draft=Config(), use_existing=True)
    result = _step_flow_picker(ctx)
    assert result.status == "skip"


def test_step_flow_picker_non_interactive_default_quickstart():
    from pythinker.cli.onboard import _step_flow_picker

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    with patch("pythinker.cli.onboard_views.clack.select") as sel:
        _step_flow_picker(ctx)

    assert ctx.flow == "quickstart"
    sel.assert_not_called()


def test_step_quickstart_summary_skipped_in_manual():
    from pythinker.cli.onboard import _step_quickstart_summary

    ctx = _WizardContext(draft=Config(), flow="manual")
    result = _step_quickstart_summary(ctx)
    assert result.status == "skip"


def test_step_quickstart_summary_renders_in_quickstart():
    from pythinker.cli.onboard import _step_quickstart_summary

    ctx = _WizardContext(draft=Config(), flow="quickstart")
    with patch("pythinker.cli.onboard_views.clack.note") as note, \
         patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_quickstart_summary(ctx)
    assert result.status == "continue"
    note.assert_called_once()
    title, body = note.call_args.args
    assert title == "QuickStart"


def test_step_provider_picker_oauth_selection_records_choice():
    from pythinker.cli.onboard import _step_provider_picker

    ctx = _WizardContext(draft=Config())
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="openai-codex",
    ):
        result = _step_provider_picker(ctx)
    assert result.status == "continue"
    assert ctx.auth == "openai-codex"


def test_step_provider_picker_skip_no_mutation():
    from pythinker.cli.onboard import _step_provider_picker

    ctx = _WizardContext(draft=Config())
    with patch("pythinker.cli.onboard_views.clack.select", return_value="skip"):
        result = _step_provider_picker(ctx)
    assert result.status == "continue"
    assert ctx.auth == "skip"


def test_step_provider_picker_non_interactive_uses_auth_flag():
    from pythinker.cli.onboard import _step_provider_picker

    ctx = _WizardContext(draft=Config(), non_interactive=True, auth="openai-codex")
    with patch("pythinker.cli.onboard_views.clack.select") as sel:
        _step_provider_picker(ctx)
    assert ctx.auth == "openai-codex"
    sel.assert_not_called()


def test_step_provider_picker_non_interactive_skip():
    from pythinker.cli.onboard import _step_provider_picker

    ctx = _WizardContext(draft=Config(), non_interactive=True, auth="skip")
    with patch("pythinker.cli.onboard_views.clack.select") as sel:
        result = _step_provider_picker(ctx)
    sel.assert_not_called()
    assert result.status == "continue"


# ---------------------------------------------------------------------------
# provider-flow.ts decorated-options port — hints surface auth style + signup
# ---------------------------------------------------------------------------


def test_build_provider_options_oauth_rows_carry_oauth_hint():
    """OAuth providers render with 'OAuth · …' in the hint column so the user
    can spot one-click flows in the picker without reading external docs.
    Mirrors the pythinker provider-flow option.hint decoration."""
    from pythinker.cli.onboard import _build_provider_options

    options = _build_provider_options()
    by_id = {opt[0]: opt for opt in options}

    codex = by_id["openai_codex"]
    copilot = by_id["github_copilot"]
    assert "OAuth" in codex[2]
    assert "ChatGPT" in codex[2]
    assert "OAuth" in copilot[2]
    assert "GitHub" in copilot[2]


def test_build_provider_options_oauth_buckets_first():
    """OAuth providers must appear before any non-OAuth row in the list so
    the wizard's first impression highlights the lowest-friction options."""
    from pythinker.cli.onboard import _build_provider_options

    options = _build_provider_options()
    ids = [opt[0] for opt in options if opt[0] != "skip"]
    codex_idx = ids.index("openai_codex")
    copilot_idx = ids.index("github_copilot")
    # Anthropic is a standard API-key provider — must come after both OAuth ones.
    anthropic_idx = ids.index("anthropic")
    assert codex_idx < anthropic_idx
    assert copilot_idx < anthropic_idx


def test_build_provider_options_signup_marker_and_skip_last():
    """Providers with a signup_url get a '↗' marker; 'skip' is always last."""
    from pythinker.cli.onboard import _build_provider_options

    options = _build_provider_options()
    assert options[-1][0] == "skip"
    by_id = {opt[0]: opt for opt in options}
    # Anthropic ships a signup_url in the registry — its hint should mention it.
    assert "signup" in by_id["anthropic"][2].lower()


def test_step_auth_method_skipped_when_no_methods():
    from pythinker.cli.onboard import _step_auth_method_picker

    ctx = _WizardContext(draft=Config(), auth="deepseek")
    result = _step_auth_method_picker(ctx)
    assert result.status == "skip"


def test_step_auth_method_skipped_when_skip_chosen():
    from pythinker.cli.onboard import _step_auth_method_picker

    ctx = _WizardContext(draft=Config(), auth="skip")
    result = _step_auth_method_picker(ctx)
    assert result.status == "skip"


def test_step_auth_method_auto_picks_when_one_method():
    from pythinker.cli.onboard import _step_auth_method_picker

    ctx = _WizardContext(draft=Config(), auth="openai_codex")
    with patch("pythinker.cli.onboard_views.clack.select") as sel, \
         patch("pythinker.cli.onboard_views.clack.print_status"), \
         patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_auth_method_picker(ctx)
    assert result.status == "continue"
    assert ctx.auth_method == "browser-login"
    sel.assert_not_called()


def test_step_auth_method_prompts_when_multiple():
    from pythinker.cli.onboard import _step_auth_method_picker

    ctx = _WizardContext(draft=Config(), auth="minimax")
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="api-key-global",
    ):
        result = _step_auth_method_picker(ctx)
    assert result.status == "continue"
    assert ctx.auth_method == "api-key-global"


def test_step_auth_method_back_returns_back_status():
    """[Back] now returns ``StepResult(status='back')`` rather than the legacy
    abort-with-marker placeholder. The orchestrator pops history and re-runs
    the provider picker so the user can pick a different provider (Phase 1
    task 5)."""
    from pythinker.cli.onboard import _step_auth_method_picker

    ctx = _WizardContext(draft=Config(), auth="minimax")
    with patch("pythinker.cli.onboard_views.clack.select", return_value="__back__"):
        result = _step_auth_method_picker(ctx)
    assert result.status == "back"


def test_step_auth_method_non_interactive_uses_flag():
    from pythinker.cli.onboard import _step_auth_method_picker

    ctx = _WizardContext(
        draft=Config(),
        auth="minimax",
        auth_method="api-key-cn",
        non_interactive=True,
    )
    with patch("pythinker.cli.onboard_views.clack.select") as sel:
        _step_auth_method_picker(ctx)
    assert ctx.auth_method == "api-key-cn"
    sel.assert_not_called()


def test_step_run_auth_skipped_when_no_provider():
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(draft=Config(), auth=None)
    result = _step_run_auth(ctx)
    assert result.status == "skip"


def test_step_run_auth_skipped_when_skip():
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(draft=Config(), auth="skip")
    result = _step_run_auth(ctx)
    assert result.status == "skip"


def test_step_run_auth_browser_login_calls_oauth_remote():
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
    )
    with patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
        return_value="tok",
    ) as login, patch("pythinker.cli.onboard_views.clack.print_status"), \
         patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_run_auth(ctx)
    assert result.status == "continue"
    login.assert_called_once_with("openai_codex")


def test_step_run_auth_api_key_paste_writes_to_config(monkeypatch):
    from pythinker.cli.onboard import _step_run_auth

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    ctx = _WizardContext(draft=Config(), auth="deepseek")

    with patch("pythinker.cli.onboard_views.clack.text", return_value="sk-xyz"):
        result = _step_run_auth(ctx)
    assert result.status == "continue"
    # Hyphenated provider names map to underscores in schema attribute access.
    pc = getattr(ctx.draft.providers, "deepseek")
    assert pc.api_key == "sk-xyz"


def test_step_run_auth_api_key_env_var_path(monkeypatch):
    from pythinker.cli.onboard import _step_run_auth

    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-tok")
    ctx = _WizardContext(draft=Config(), auth="deepseek")

    with patch(
        "pythinker.cli.onboard_views.clack.confirm",
        return_value=True,
    ):
        result = _step_run_auth(ctx)
    assert result.status == "continue"
    # Env-var indirection writes ${VAR} ref, not the literal.
    pc = getattr(ctx.draft.providers, "deepseek")
    assert pc.api_key == "${DEEPSEEK_API_KEY}"


def test_step_run_auth_oauth_failure_returns_abort():
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
    )
    with patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
        side_effect=RuntimeError("oauth failed"),
    ):
        result = _step_run_auth(ctx)
    assert result.status == "abort"


def test_step_default_model_keep_current_no_change():
    from pythinker.cli.onboard import _KEEP_KEY, _step_default_model

    ctx = _WizardContext(draft=Config(), auth="openai-codex")
    ctx.draft.agents.defaults.model = "openai-codex/gpt-5.5"
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value=_KEEP_KEY,
    ):
        result = _step_default_model(ctx)
    assert result.status == "continue"
    assert ctx.draft.agents.defaults.model == "openai-codex/gpt-5.5"


def test_step_default_model_enter_manually():
    from pythinker.cli.onboard import _MANUAL_KEY, _step_default_model

    ctx = _WizardContext(draft=Config(), auth="openai-codex")
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value=_MANUAL_KEY,
    ), patch(
        "pythinker.cli.onboard_views.clack.text",
        return_value="some-model/v1",
    ):
        result = _step_default_model(ctx)
    assert result.status == "continue"
    assert ctx.draft.agents.defaults.model == "some-model/v1"


def test_step_default_model_browse_all():
    """Inline-picker design (mirrors pythinker): one select returns the chosen
    model id directly. The user picks the actual model from a single list
    instead of stepping through a 'browse' meta-menu."""
    from pythinker.cli.onboard import _step_default_model

    ctx = _WizardContext(draft=Config(), auth="openai-codex")

    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="openai-codex/gpt-5.5",
    ), patch(
        "pythinker.cli.onboard.get_model_suggestions",
        return_value=["openai-codex/gpt-5.5", "openai-codex/gpt-4o"],
    ):
        _step_default_model(ctx)
    assert ctx.draft.agents.defaults.model == "openai-codex/gpt-5.5"


def test_step_default_model_inline_default_jumps_to_provider_when_mismatch():
    """When the carried-over draft model belongs to a different provider,
    initial cursor jumps to the first recommended model for the new provider
    (mirrors pythinker's ``initialValue`` mismatch logic)."""
    from pythinker.cli.onboard import _step_default_model

    ctx = _WizardContext(draft=Config(), auth="openai_codex")
    ctx.draft.agents.defaults.model = "MiniMax-M2.7"

    captured: dict = {}

    def _capture_select(question, *, options, default, **_kwargs):
        captured["question"] = question
        captured["options"] = options
        captured["default"] = default
        return default  # accept the highlighted default

    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=_capture_select,
    ), patch(
        "pythinker.cli.onboard.get_model_suggestions",
        return_value=["openai-codex/gpt-5.5-mini", "openai-codex/gpt-5.4-mini"],
    ):
        _step_default_model(ctx)

    # The picker must default to the first provider-native model, not Keep.
    assert captured["default"] == "openai-codex/gpt-5.5-mini"
    assert ctx.draft.agents.defaults.model == "openai-codex/gpt-5.5-mini"
    # Keep entry is still listed (with mismatch warning), Manual is too.
    option_ids = [opt[0] for opt in captured["options"]]
    assert "__keep__" in option_ids
    assert "__manual__" in option_ids
    keep_row = next(opt for opt in captured["options"] if opt[0] == "__keep__")
    assert "warning" in keep_row[2].lower()


def test_step_default_model_skipped_when_no_auth():
    from pythinker.cli.onboard import _step_default_model

    ctx = _WizardContext(draft=Config(), auth=None)
    result = _step_default_model(ctx)
    assert result.status == "skip"


def test_step_default_model_non_interactive_keeps_current():
    from pythinker.cli.onboard import _step_default_model

    ctx = _WizardContext(draft=Config(), auth="openai-codex", non_interactive=True)
    ctx.draft.agents.defaults.model = "openai-codex/gpt-5.5"
    with patch("pythinker.cli.onboard_views.clack.select") as sel:
        result = _step_default_model(ctx)
    assert result.status == "continue"
    sel.assert_not_called()


def test_step_workspace_default_accepts(tmp_path):
    from pythinker.cli.onboard import _step_workspace

    ws = tmp_path / "ws"
    ctx = _WizardContext(draft=Config())
    with patch("pythinker.cli.onboard_views.clack.text", return_value=str(ws)):
        result = _step_workspace(ctx)
    assert result.status == "continue"
    assert ws.exists()


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX chmod 0o500 doesn't make a directory unwritable on NTFS",
)
def test_step_workspace_unwritable_re_prompts(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_workspace

    bad = tmp_path / "readonly"
    bad.mkdir()
    bad.chmod(0o500)

    ws = tmp_path / "writable"

    inputs = iter([str(bad / "child"), str(ws)])
    ctx = _WizardContext(draft=Config())
    try:
        with patch(
            "pythinker.cli.onboard_views.clack.text",
            side_effect=lambda *a, **kw: next(inputs),
        ), patch("pythinker.cli.onboard_views.clack.print_status"):
            result = _step_workspace(ctx)
    finally:
        bad.chmod(0o700)  # cleanup so tmp_path can be removed

    assert result.status == "continue"
    assert ws.exists()


def test_step_workspace_non_interactive_uses_default(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_workspace

    ws = tmp_path / "non-interactive-ws"
    ctx = _WizardContext(draft=Config(), non_interactive=True)
    ctx.draft.agents.defaults.workspace = str(ws)
    result = _step_workspace(ctx)
    assert result.status == "continue"
    assert ws.exists()


def test_step_channels_skipped_in_quickstart():
    from pythinker.cli.onboard import _step_channels

    ctx = _WizardContext(draft=Config(), flow="quickstart")
    result = _step_channels(ctx)
    assert result.status == "skip"


def test_step_channels_none_selected_continues():
    """User opens the channel picker and immediately picks 'Done' — no per-channel
    configurator should run, and the step returns 'continue' without mutating draft."""
    from pythinker.cli.onboard import _step_channels

    ctx = _WizardContext(draft=Config(), flow="manual")
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="__done__",
    ), patch("pythinker.cli.onboard._configure_channel") as mock_configure, \
       patch("pythinker.cli.onboard_views.clack.note"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_channels(ctx)
    assert result.status == "continue"
    mock_configure.assert_not_called()


def test_step_channels_telegram_routes_to_configurator():
    """Picking telegram dispatches into ``_configure_channel`` for that channel,
    then loops back; once the user picks Done the step returns. Per-field secret
    handling (inline/env) is owned by ``_configure_channel`` and tested there."""
    from pythinker.cli.onboard import _step_channels

    ctx = _WizardContext(draft=Config(), flow="manual")
    selects = iter(["telegram", "__done__"])
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch("pythinker.cli.onboard._configure_channel") as mock_configure, \
       patch("pythinker.cli.onboard_views.clack.note"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_channels(ctx)
    assert result.status == "continue"
    mock_configure.assert_called_once()
    args, _ = mock_configure.call_args
    assert args[1] == "telegram"


def test_step_channels_done_first_iteration_skips_configurator():
    """If the channel picker is shown but the user backs out without picking
    any channel, the step returns 'continue' and never invokes the per-channel
    configurator (regression guard against an extra prompt)."""
    from pythinker.cli.onboard import _step_channels

    ctx = _WizardContext(draft=Config(), flow="manual")
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="__done__",
    ), patch("pythinker.cli.onboard._configure_channel") as mock_configure, \
       patch("pythinker.cli.onboard_views.clack.note"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        _step_channels(ctx)
    mock_configure.assert_not_called()


# ---------------------------------------------------------------------------
# Channel "already configured" disambiguation picker
# ---------------------------------------------------------------------------


def _enable_telegram_on_draft(draft: Config) -> None:
    """Helper: flip the telegram channel to enabled on a Config draft so the
    picker treats it as 'already configured' and routes through the new prompt.

    ``ChannelsConfig`` uses pydantic ``extra="allow"`` for per-channel sub-blocks,
    so we add a dict-shaped ``telegram`` extra rather than relying on a named field.
    """
    # pydantic's extra="allow" allows __setattr__ to land in __pydantic_extra__.
    setattr(draft.channels, "telegram", {"enabled": True, "token": "stub"})


def test_step_channels_already_configured_skip_leaves_config_untouched():
    """If the user picks an already-configured channel and chooses 'Skip',
    the per-field editor must not run and the channel stays enabled."""
    from pythinker.cli.onboard import _step_channels

    ctx = _WizardContext(draft=Config(), flow="manual")
    _enable_telegram_on_draft(ctx.draft)

    selects = iter(["telegram", "skip", "__done__"])
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch("pythinker.cli.onboard._configure_channel") as mock_configure, \
       patch("pythinker.cli.onboard_views.clack.note"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_channels(ctx)

    assert result.status == "continue"
    mock_configure.assert_not_called()
    # Skip = leave as-is, including the enabled flag.
    from pythinker.cli.onboard import _channel_is_enabled
    assert _channel_is_enabled(ctx.draft, "telegram") is True


def test_step_channels_already_configured_disable_flips_enabled_flag():
    """Picking 'Disable (keeps config)' must set ``enabled=False`` without
    invoking the editor, then loop back to the channel picker."""
    from pythinker.cli.onboard import _channel_is_enabled, _step_channels

    ctx = _WizardContext(draft=Config(), flow="manual")
    _enable_telegram_on_draft(ctx.draft)
    assert _channel_is_enabled(ctx.draft, "telegram") is True

    selects = iter(["telegram", "disable", "__done__"])
    statuses: list[str] = []
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch("pythinker.cli.onboard._configure_channel") as mock_configure, \
       patch(
        "pythinker.cli.onboard_views.clack.print_status",
        side_effect=statuses.append,
    ), patch("pythinker.cli.onboard_views.clack.note"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_channels(ctx)

    assert result.status == "continue"
    mock_configure.assert_not_called()
    assert _channel_is_enabled(ctx.draft, "telegram") is False
    assert any("disabled" in s.lower() for s in statuses), (
        f"expected a 'disabled' status message, got: {statuses}"
    )


def test_prompt_configured_action_default_options_and_default_value():
    """The generalized helper offers update / disable / skip with 'update'
    pre-selected (mirrors pythinker promptConfiguredAction defaults)."""
    from pythinker.cli.onboard import _prompt_configured_action

    captured = {}

    def fake_select(question, *, options, default, **_kwargs):
        captured["question"] = question
        captured["options"] = options
        captured["default"] = default
        return default

    with patch("pythinker.cli.onboard_views.clack.select", side_effect=fake_select):
        result = _prompt_configured_action("Telegram channel")

    assert result == "update"
    assert "Telegram channel" in captured["question"]
    assert captured["default"] == "update"
    ids = [opt[0] for opt in captured["options"]]
    assert ids == ["update", "disable", "skip"]


def test_prompt_configured_action_supports_disable_false_omits_row():
    """When the caller sets supports_disable=False, the picker omits the
    Disable row entirely — used by the auth-re-auth surface where 'disable'
    has no clean semantics."""
    from pythinker.cli.onboard import _prompt_configured_action

    captured = {}

    def fake_select(question, *, options, default, **_kwargs):
        captured["options"] = options
        return "skip"

    with patch("pythinker.cli.onboard_views.clack.select", side_effect=fake_select):
        _prompt_configured_action("Anthropic auth", supports_disable=False)

    ids = [opt[0] for opt in captured["options"]]
    assert ids == ["update", "skip"]


def test_step_run_auth_skips_oauth_when_already_authenticated_and_user_picks_skip():
    """If the provider already has a valid credential and the user chooses
    'Skip', _step_run_auth returns continue without invoking the OAuth flow
    or prompting for an API key. Mirrors the configured-action flow on the
    auth surface (Phase 1 task 4)."""
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
    )

    with patch(
        "pythinker.auth.credential_source",
        return_value="oauth",
    ), patch(
        "pythinker.cli.onboard._prompt_configured_action",
        return_value="skip",
    ) as mock_prompt, patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
    ) as mock_oauth, patch(
        "pythinker.cli.onboard_views.clack.bar_break",
    ):
        result = _step_run_auth(ctx)

    assert result.status == "continue"
    mock_prompt.assert_called_once()
    mock_oauth.assert_not_called()


def test_step_run_auth_re_runs_oauth_when_user_picks_update():
    """'Update' falls through to the normal auth path — the OAuth flow runs
    even though the provider was already authenticated."""
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
    )

    with patch(
        "pythinker.auth.credential_source",
        return_value="oauth",
    ), patch(
        "pythinker.cli.onboard._prompt_configured_action",
        return_value="update",
    ), patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
    ) as mock_oauth, patch(
        "pythinker.cli.onboard_views.clack.bar_break",
    ), patch(
        "pythinker.cli.onboard_views.clack.print_status",
    ):
        result = _step_run_auth(ctx)

    assert result.status == "continue"
    mock_oauth.assert_called_once_with("openai_codex")


def test_step_run_auth_does_not_prompt_when_unauthenticated():
    """First-time auth runs the OAuth flow directly without showing the
    'already configured' picker (regression guard)."""
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
    )

    with patch(
        "pythinker.auth.credential_source",
        return_value="none",
    ), patch(
        "pythinker.cli.onboard._prompt_configured_action",
    ) as mock_prompt, patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
    ) as mock_oauth, patch(
        "pythinker.cli.onboard_views.clack.bar_break",
    ), patch(
        "pythinker.cli.onboard_views.clack.print_status",
    ):
        result = _step_run_auth(ctx)

    assert result.status == "continue"
    mock_prompt.assert_not_called()
    mock_oauth.assert_called_once()


def test_step_run_auth_non_interactive_skips_configured_prompt():
    """In headless mode the configured-prompt is suppressed even when the
    provider is already authenticated — non-interactive runs default to
    update semantics so explicit re-auth flags can take effect."""
    from pythinker.cli.onboard import _step_run_auth

    ctx = _WizardContext(
        draft=Config(),
        auth="openai-codex",
        auth_method="browser-login",
        non_interactive=True,
    )

    with patch(
        "pythinker.auth.credential_source",
        return_value="oauth",
    ), patch(
        "pythinker.cli.onboard._prompt_configured_action",
    ) as mock_prompt, patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
    ), patch(
        "pythinker.cli.onboard_views.clack.bar_break",
    ), patch(
        "pythinker.cli.onboard_views.clack.print_status",
    ):
        _step_run_auth(ctx)

    mock_prompt.assert_not_called()


def test_step_channels_already_configured_update_falls_through_to_editor():
    """Picking 'Modify settings' must drop into ``_configure_channel`` exactly
    once for the picked channel — same as the unconfigured path."""
    from pythinker.cli.onboard import _step_channels

    ctx = _WizardContext(draft=Config(), flow="manual")
    _enable_telegram_on_draft(ctx.draft)

    selects = iter(["telegram", "update", "__done__"])
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        side_effect=lambda *a, **kw: next(selects),
    ), patch("pythinker.cli.onboard._configure_channel") as mock_configure, \
       patch("pythinker.cli.onboard_views.clack.note"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        _step_channels(ctx)

    mock_configure.assert_called_once()
    args, _ = mock_configure.call_args
    assert args[1] == "telegram"


def test_step_search_skipped_in_quickstart():
    from pythinker.cli.onboard import _step_search_provider

    ctx = _WizardContext(draft=Config(), flow="quickstart")
    result = _step_search_provider(ctx)
    assert result.status == "skip"


def test_step_search_skip_for_now_no_mutation():
    from pythinker.cli.onboard import _step_search_provider

    ctx = _WizardContext(draft=Config(), flow="manual")
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="skip",
    ):
        result = _step_search_provider(ctx)
    assert result.status == "continue"


def test_step_search_tavily_inline_key():
    from pythinker.cli.onboard import _step_search_provider

    ctx = _WizardContext(draft=Config(), flow="manual")
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="tavily",
    ), patch(
        "pythinker.cli.onboard_views.clack.text",
        return_value="tvly-abc",
    ), patch("pythinker.cli.onboard_views.clack.print_status"):
        result = _step_search_provider(ctx)
    assert result.status == "continue"


def test_step_summary_save_writes_config(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)

    saved = []
    monkeypatch.setattr(
        "pythinker.cli.onboard.save_config",
        lambda cfg, path: saved.append((cfg, path)),
    )

    ctx = _WizardContext(draft=Config())
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="save",
    ), patch("pythinker.cli.onboard_views.summary.render_pre_save"), \
       patch("pythinker.cli.onboard_views.clack.print_status"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_summary_confirm(ctx)
    assert result.status == "continue"
    assert len(saved) == 1


def test_step_summary_skip_does_not_write(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    saved = []
    monkeypatch.setattr(
        "pythinker.cli.onboard.save_config",
        lambda cfg, path: saved.append((cfg, path)),
    )

    ctx = _WizardContext(draft=Config())
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="skip",
    ), patch("pythinker.cli.onboard_views.summary.render_pre_save"):
        result = _step_summary_confirm(ctx)
    assert result.status == "abort"
    assert saved == []


def test_step_summary_reset_pending_renames_config(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("old")
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr("pythinker.cli.onboard.save_config", lambda cfg, p: None)

    ctx = _WizardContext(draft=Config(), reset_pending=True)
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="save",
    ), patch("pythinker.cli.onboard_views.summary.render_pre_save"), \
       patch("pythinker.cli.onboard_views.clack.print_status"), \
       patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_summary_confirm(ctx)
    assert result.status == "continue"
    backups = list(cfg_path.parent.glob("config.json.bak.*"))
    assert len(backups) == 1


def test_step_summary_non_interactive_auto_saves(tmp_path, monkeypatch):
    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    saved = []
    monkeypatch.setattr(
        "pythinker.cli.onboard.save_config",
        lambda cfg, path: saved.append((cfg, path)),
    )

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    with patch("pythinker.cli.onboard_views.clack.select") as sel, \
         patch("pythinker.cli.onboard_views.summary.render_pre_save"), \
         patch("pythinker.cli.onboard_views.clack.print_status"), \
         patch("pythinker.cli.onboard_views.clack.bar_break"):
        _step_summary_confirm(ctx)
    sel.assert_not_called()
    assert len(saved) == 1


def test_step_start_gateway_no_skips_handoff(monkeypatch):
    """Picking 'no' must never replace the wizard process — control returns to user."""
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config())
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="no",
    ), patch("os.execvp") as handoff, \
         patch("pythinker.cli.onboard_views.clack.print_status"):
        result = _step_start_gateway(ctx)
    assert result.status == "continue"
    handoff.assert_not_called()


def test_step_start_gateway_yes_replaces_process_with_gateway(monkeypatch):
    """Picking 'yes' must hand control to ``pythinker gateway`` via os.execvp.

    Real os.execvp would never return; the patch makes it a no-op so the
    function falls through. What's pinned: the call happened, with the
    correct argv shape.
    """
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config())
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="yes",
    ), patch("os.execvp") as handoff, \
         patch("pythinker.cli.onboard_views.clack.print_status"):
        _step_start_gateway(ctx)

    handoff.assert_called_once()
    args, _kwargs = handoff.call_args
    # First positional is the python interpreter, second is argv.
    assert args[1][1:] == ["-m", "pythinker", "gateway"]


def test_step_start_gateway_yes_open_webui_spawns_browser_helper(monkeypatch):
    """When --open-webui is set, a detached browser-poll helper is spawned
    *before* the process replacement so it survives the handoff."""
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config(), open_webui=True)
    with patch(
        "pythinker.cli.onboard_views.clack.select",
        return_value="yes",
    ), patch("os.execvp") as handoff, \
         patch("subprocess.Popen") as popen, \
         patch("pythinker.cli.onboard_views.clack.print_status"):
        _step_start_gateway(ctx)

    popen.assert_called_once()
    handoff.assert_called_once()


def test_step_start_gateway_skip_flag_no_op(monkeypatch):
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config(), skip_gateway=True)
    with patch("os.execvp") as handoff, patch("subprocess.Popen") as popen:
        result = _step_start_gateway(ctx)
    assert result.status == "continue"
    handoff.assert_not_called()
    popen.assert_not_called()


def test_step_start_gateway_non_interactive_skip(monkeypatch):
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config(), non_interactive=True, start_gateway=None)
    with patch("os.execvp") as handoff, patch("subprocess.Popen") as popen:
        result = _step_start_gateway(ctx)
    assert result.status == "continue"
    handoff.assert_not_called()
    popen.assert_not_called()


# End-to-end happy-path tests


def test_e2e_first_run_quickstart_codex_oauth(tmp_path, monkeypatch):
    """Fresh install → QuickStart → Codex OAuth (mocked) → config written."""
    from pythinker.cli.onboard import run_onboard

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(
        "pythinker.cli.onboard.get_config_path", lambda: cfg_path
    )

    with patch(
        "pythinker.cli.onboard._login_via_oauth_remote",
        return_value="fake-token",
    ):
        result = run_onboard(
            Config(),
            non_interactive=True,
            flow="quickstart",
            yes_security=True,
            auth="openai-codex",
            auth_method="browser-login",
            skip_gateway=True,
        )

    assert cfg_path.exists()
    assert result.config is not None


def test_e2e_noop_when_config_exists_and_use_existing_flag(tmp_path, monkeypatch):
    """When --use-existing-committed is True, skip past existing-config step."""
    import json

    from pythinker.cli.onboard import _run_linear_wizard, _WizardContext

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"version": 1}))
    monkeypatch.setattr(
        "pythinker.cli.onboard.get_config_path", lambda: cfg_path
    )
    monkeypatch.setattr(
        "pythinker.cli.onboard.load_config",
        lambda *a, **kw: Config(),
    )

    saves = []
    monkeypatch.setattr(
        "pythinker.cli.onboard.save_config",
        lambda cfg, path: saves.append((cfg, path)),
    )

    ctx = _WizardContext(draft=Config(), use_existing=True)
    with patch(
        "pythinker.cli.onboard_views.clack.print_status"
    ), patch(
        "pythinker.cli.onboard_views.clack.bar_break"
    ):
        result = _run_linear_wizard(ctx)

    assert result is not None
    assert result.should_save is False


def test_e2e_headless_install_smoke_path(tmp_path, monkeypatch):
    """The exact CI command must succeed: --non-interactive --flow quickstart --yes-security --skip-gateway."""
    from pythinker.cli.onboard import run_onboard

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(
        "pythinker.cli.onboard.get_config_path", lambda: cfg_path
    )

    result = run_onboard(
        Config(),
        non_interactive=True,
        flow="quickstart",
        yes_security=True,
        skip_gateway=True,
    )
    assert cfg_path.exists()
    assert result.config is not None


# ---------------------------------------------------------------------------
# Item #18 — --workspace override on Update/Reset paths
# ---------------------------------------------------------------------------


def test_workspace_override_wins_over_loaded_config(tmp_path, monkeypatch):
    """workspace_override in ctx takes priority over any value in ctx.draft."""
    from pythinker.cli.onboard import _step_workspace

    override = str(tmp_path / "my-ws")
    ctx = _WizardContext(draft=Config(), non_interactive=True, workspace_override=override)
    # draft has no workspace set; override should be used as the default and accepted.
    with patch("pythinker.cli.onboard_views.clack.print_status"):
        result = _step_workspace(ctx)

    assert result.status == "continue"
    assert ctx.draft.agents.defaults.workspace == str(tmp_path / "my-ws")


def test_workspace_override_wins_over_draft_workspace(tmp_path, monkeypatch):
    """workspace_override beats a pre-existing value in ctx.draft when on Update/Reset path."""
    from pythinker.cli.onboard import _step_workspace

    old_ws = str(tmp_path / "old-ws")
    new_ws = str(tmp_path / "new-ws")
    cfg = Config()
    cfg.agents.defaults.workspace = old_ws
    ctx = _WizardContext(draft=cfg, non_interactive=True, workspace_override=new_ws)
    with patch("pythinker.cli.onboard_views.clack.print_status"):
        result = _step_workspace(ctx)

    assert result.status == "continue"
    assert ctx.draft.agents.defaults.workspace == str(tmp_path / "new-ws")


# ---------------------------------------------------------------------------
# Item #15 — WebUI browser auto-open at end
# ---------------------------------------------------------------------------


def test_step_start_gateway_open_webui_schedules_browser_helper():
    """When --open-webui is set, a detached browser-poll helper subprocess is
    spawned right before the foreground gateway handoff. Pre-foreground
    refactor this lived in _step_outro; it moved into _step_start_gateway so
    the helper can poll /health on the about-to-start gateway and survive
    the os.execvp process replacement (it gets its own session)."""
    from pythinker.cli.onboard import _step_start_gateway

    draft = Config()
    draft.gateway.port = 18888
    ctx = _WizardContext(draft=draft, open_webui=True)
    with patch(
        "pythinker.cli.onboard_views.clack.select", return_value="yes"
    ), patch("pythinker.cli.onboard_views.clack.print_status"), \
            patch("subprocess.Popen") as popen, \
            patch("os.execvp"):
        _step_start_gateway(ctx)

    popen.assert_called_once()
    helper_argv = popen.call_args[0][0]
    # Helper is spawned as `python -c "<browser-poll script>"`.
    assert helper_argv[1] == "-c"
    assert "webbrowser.open" in helper_argv[2]
    assert "18888" in helper_argv[2]


def test_step_start_gateway_no_open_webui_skips_browser_helper():
    """No --open-webui flag → no detached browser helper subprocess."""
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config(), open_webui=False)
    with patch(
        "pythinker.cli.onboard_views.clack.select", return_value="yes"
    ), patch("pythinker.cli.onboard_views.clack.print_status"), \
            patch("subprocess.Popen") as popen, \
            patch("os.execvp"):
        _step_start_gateway(ctx)

    popen.assert_not_called()


def test_step_start_gateway_open_webui_skipped_when_user_declines():
    """When user picks 'no' at the gateway prompt, browser helper is not spawned
    even with --open-webui — there's nothing for it to open against."""
    from pythinker.cli.onboard import _step_start_gateway

    ctx = _WizardContext(draft=Config(), open_webui=True)
    with patch(
        "pythinker.cli.onboard_views.clack.select", return_value="no"
    ), patch("pythinker.cli.onboard_views.clack.print_status"), \
            patch("subprocess.Popen") as popen, \
            patch("os.execvp"):
        _step_start_gateway(ctx)

    popen.assert_not_called()


# ---------------------------------------------------------------------------
# Item #12 — sha256-aware config-overwrite logging
# ---------------------------------------------------------------------------


def test_step_summary_logs_sha256_after_save(tmp_path, monkeypatch):
    """_step_summary_confirm emits a sha256 log with old->new hashes when overwriting."""
    import hashlib
    import json as _json

    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    existing_content = _json.dumps({"version": 1}).encode()
    cfg_path.write_bytes(existing_content)
    old_sha = hashlib.sha256(existing_content).hexdigest()[:12]

    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    saved = []
    monkeypatch.setattr(
        "pythinker.cli.onboard.save_config",
        lambda cfg, path: (saved.append(cfg), cfg_path.write_text(_json.dumps({"version": 2}))),
    )

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    logged = []
    with patch("pythinker.cli.onboard_views.clack.print_status", side_effect=logged.append), \
            patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_summary_confirm(ctx)

    assert result.status == "continue"
    assert logged, "expected at least one clack.print_status call"
    status_msg = logged[0]
    assert "sha256" in status_msg
    assert old_sha in status_msg
    assert "->" in status_msg


def test_step_summary_fresh_save_logs_saved_not_updated(tmp_path, monkeypatch):
    """Fresh installs should say Saved, not Updated."""
    import json as _json

    from pythinker.cli.onboard import _step_summary_confirm

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr(
        "pythinker.cli.onboard.save_config",
        lambda _cfg, _path: cfg_path.write_text(_json.dumps({"version": 1})),
    )

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    logged = []
    with patch("pythinker.cli.onboard_views.clack.print_status", side_effect=logged.append), \
            patch("pythinker.cli.onboard_views.clack.bar_break"):
        result = _step_summary_confirm(ctx)

    assert result.status == "continue"
    assert logged
    assert logged[0].startswith("Saved ")
