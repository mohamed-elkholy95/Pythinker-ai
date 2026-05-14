"""Banner / intro / outro steps for the onboarding wizard."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_banner(ctx: _WizardContext) -> StepResult:
    """Print the polished first-run setup panel."""
    from pythinker import __version__
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views.panels import render_welcome_panel

    render_welcome_panel(
        _onboard.console,
        version=__version__,
        config_path=_onboard.get_config_path(),
        workspace=ctx.workspace_override or ctx.draft.agents.defaults.workspace,
        flow=ctx.flow,
        non_interactive=ctx.non_interactive,
    )
    return StepResult(status="continue")


def _step_intro(ctx: _WizardContext) -> StepResult:
    """Open the persistent bar with the wizard title."""
    from pythinker.cli.onboard_views import clack

    clack.intro("Pythinker setup")
    return StepResult(status="continue")


def _step_outro(ctx: _WizardContext) -> StepResult:
    """Close the bar with next-steps guidance.

    Runs *before* ``_step_start_gateway`` (which execs into the gateway
    and never returns), so the next-steps message is the last thing the
    user sees before the gateway takes over the terminal. The
    ``open_webui`` browser handoff was moved to ``_step_start_gateway``
    where it can poll /health on the about-to-start gateway.
    """
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack

    clack.outro("🐍 Pythinker is ready.")
    _onboard.console.print("\nNext:")
    _onboard.console.print("  pythinker tui         full-screen chat")
    _onboard.console.print("  pythinker agent       terminal chat / one-shot prompts")
    _onboard.console.print("  pythinker gateway     start channels + API")
    _onboard.console.print("  pythinker doctor      verify your setup anytime\n")
    return StepResult(status="continue")
