"""Flow picker (QuickStart vs Manual) + QuickStart summary steps."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_flow_picker(ctx: _WizardContext) -> StepResult:
    """Step 5 — choose QuickStart vs Manual setup mode."""
    from pythinker.cli.onboard_views import clack

    if ctx.use_existing:
        return StepResult(status="skip")

    if ctx.non_interactive and ctx.flow == "manual":
        # _WizardContext default for `flow` is "manual"; flip to quickstart
        # when running non-interactively without an explicit --flow value.
        ctx.flow = "quickstart"
        return StepResult(status="continue")

    if ctx.flow != "manual":
        # User passed `--flow quickstart` (or some other explicit value).
        return StepResult(status="continue")

    chosen = clack.select(
        "Setup mode",
        options=[
            ("quickstart", "QuickStart", "Minimal prompts. Defaults for workspace, gateway, channels."),
            ("manual", "Manual", "Walk every section."),
        ],
        default="quickstart",
    )
    ctx.flow = chosen
    return StepResult(status="continue")


def _step_quickstart_summary(ctx: _WizardContext) -> StepResult:
    """Step 6 — show defaults preview when user picked QuickStart."""
    from pythinker.cli.onboard_views import clack

    if ctx.flow != "quickstart":
        return StepResult(status="skip")

    body = [
        "QuickStart keeps the OpenClaw-style clean path: pick model/auth, confirm workspace, review, health-check, then launch.",
        "",
        "1. Model/Auth: choose a provider and credential path.",
        "2. Default model: use a recommended model or enter one manually.",
        "3. Workspace: ~/.pythinker/workspace unless overridden.",
        "4. Gateway: loopback on 127.0.0.1:18790.",
        "5. Channels: configured later via `pythinker onboard --flow manual`.",
        "6. Review + health: redacted diff, workspace check, auth/model check, port check.",
    ]
    clack.note("QuickStart", body)
    clack.bar_break()
    return StepResult(status="continue")
