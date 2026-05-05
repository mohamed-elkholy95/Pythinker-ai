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
        "Gateway port: 18790",
        "Gateway bind: Loopback (127.0.0.1)",
        "Workspace: ~/.pythinker/workspace",
        "Channels: configured later via `pythinker onboard` (Manual mode).",
        "Web search: deferred",
    ]
    clack.note("QuickStart", body)
    clack.bar_break()
    return StepResult(status="continue")
