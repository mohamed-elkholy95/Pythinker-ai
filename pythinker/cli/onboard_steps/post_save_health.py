"""Post-save health-check step."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _check_gateway_port_free(host: str, port: int) -> tuple[str, str]:
    """Best-effort port preflight that does NOT exit. Returns ``(status, detail)``
    where status is ``"ok"`` / ``"warn"`` / ``"error"``. Used by the post-save
    health check so a port conflict shows up as a warning (config is fine,
    the user just needs to free the port before starting the gateway), not
    as a hard failure on the wizard."""
    import errno
    import socket

    bind_host = host or "127.0.0.1"
    fam = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(fam, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, port))
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            return ("warn", f"{bind_host}:{port} already in use")
        if exc.errno == errno.EACCES:
            return ("warn", f"{bind_host}:{port} permission denied")
        return ("warn", f"bind failed ({exc.errno})")
    sock.close()
    return ("ok", f"{bind_host}:{port} free")


def _step_post_save_health(ctx: _WizardContext) -> StepResult:
    """Step 15 — green/yellow/red health check on the just-saved config.

    Inlines the relevant subset of ``pythinker doctor`` so the wizard ends
    on a confidence-building summary rather than dropping the user back at
    the shell.

    Skipped when the user discarded changes (``use_existing`` and no save).
    Provider-ping is intentionally NOT included by default — making a
    network call as the last step of onboarding hides cost and would
    slow headless installs. Add it later behind an opt-in flag.
    """
    from pythinker.cli.doctor import (
        _check_default_model,
        _check_default_provider_auth,
        _check_workspace,
    )
    from pythinker.cli.onboard_views import clack

    if ctx.use_existing:
        return StepResult(status="skip")

    glyph = {"ok": "✓", "warn": "⚠", "error": "✗"}

    def _emit(status: str, label: str, detail: str = "", fix: str = "") -> None:
        line = f"{glyph.get(status, '?')} {label}"
        if detail:
            line += f": {detail}"
        clack.print_status(line)
        if fix and status != "ok":
            clack.print_status(f"  Fix: {fix}")

    clack.print_status("Health check:")
    try:
        ws = _check_workspace()
        _emit(ws.status, ws.label, ws.detail, ws.fix)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Workspace", f"check skipped ({exc})")

    try:
        model = _check_default_model()
        _emit(model.status, model.label, model.detail, model.fix)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Default model", f"check skipped ({exc})")

    try:
        for auth_result in _check_default_provider_auth():
            _emit(auth_result.status, auth_result.label, auth_result.detail, auth_result.fix)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Provider auth", f"check skipped ({exc})")

    try:
        port_status, port_detail = _check_gateway_port_free(
            ctx.draft.gateway.host, ctx.draft.gateway.port
        )
        _emit(port_status, "Gateway port", port_detail)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Gateway port", f"check skipped ({exc})")

    clack.note(
        "Ready to launch",
        [
            "pythinker tui       full-screen chat",
            "pythinker agent     terminal chat / one-shot prompts",
            "pythinker gateway   API + WebUI + chat channels",
            "pythinker doctor    re-run these checks later",
        ],
    )
    clack.bar_break()
    return StepResult(status="continue")
