"""Start-gateway (optional exec) step."""

from __future__ import annotations

import os
import subprocess
import sys

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_start_gateway(ctx: _WizardContext) -> StepResult:
    """Step 16 — optionally hand control to the gateway in the foreground.

    Replaces the wizard process with ``pythinker gateway`` via ``os.execvp``
    so the user sees gateway logs directly, can Ctrl-C cleanly, and no
    orphan PID is left behind. Background spawning was the previous
    behavior — it produced two long-running UX issues:

      * stdout/stderr were sent to /dev/null, so first-time users saw
        nothing after onboard finished and had no way to tell whether the
        gateway had actually started.
      * The orphaned PID kept running with the wizard's just-saved config
        loaded once at startup, so any later config edit appeared to "not
        save" until the user manually killed and restarted the process.

    Skipped when --skip-gateway is set, or in non-interactive mode unless
    --start-gateway is passed. Outro runs *before* this step (see step
    registration below) so the next-steps message survives the exec.
    """
    from pythinker.cli.onboard_views import clack

    if ctx.use_existing or ctx.skip_gateway:
        return StepResult(status="continue")

    if ctx.start_gateway is None and ctx.non_interactive:
        return StepResult(status="continue")

    if ctx.start_gateway is None:
        chosen = clack.select(
            "Start the gateway now?",
            options=[
                ("yes", "Yes, start it now (Ctrl-C to stop)", ""),
                ("no", "No, I'll start it later", ""),
            ],
            default="yes",
        )
        if chosen == "no":
            clack.print_status("Start it later with: pythinker gateway")
            return StepResult(status="continue")

    # Resolve port (best-effort; default 18790).
    port_num = ctx.draft.gateway.port

    # Best-effort browser open: spawn a tiny detached helper that polls
    # /health and opens the URL once the gateway responds. Survives the
    # upcoming execvp because it's its own session.
    if ctx.open_webui:
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import time, urllib.request, webbrowser\n"
                        f"url = 'http://127.0.0.1:{port_num}'\n"
                        f"health = 'http://127.0.0.1:{port_num}/health'\n"
                        "deadline = time.monotonic() + 15.0\n"
                        "while time.monotonic() < deadline:\n"
                        "    try:\n"
                        "        urllib.request.urlopen(health, timeout=0.4)\n"
                        "        webbrowser.open(url)\n"
                        "        break\n"
                        "    except Exception:\n"
                        "        time.sleep(0.5)\n"
                    ),
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            clack.print_status(f"○ Could not schedule browser open: {exc}")

    clack.print_status(f"Starting gateway on port {port_num} — Ctrl-C to stop")

    # Replace the wizard process. From this point on, output is the
    # gateway's. Anything after this line never executes on success.
    try:
        os.execvp(sys.executable, [sys.executable, "-m", "pythinker", "gateway"])
    except OSError as exc:
        from pythinker.cli.onboard_views.errors import render_actionable

        render_actionable(
            what=f"Could not start gateway: {exc}",
            why=(
                "The wizard finished saving config but the gateway process "
                "failed to launch — your settings are saved, just not yet running."
            ),
            how=(
                "Start it manually with `pythinker gateway`. If that also fails, "
                "check that the configured port is free and that the python "
                "interpreter is on PATH."
            ),
        )
        return StepResult(status="continue")
