"""Banner / intro / outro steps for the onboarding wizard."""

from __future__ import annotations

import os

from pythinker.cli.onboard_types import StepResult, _WizardContext

_BANNER = r"""
██████  ██    ██ ████████ ██   ██ ██ ███    ██ ██   ██ ███████ ██████
██   ██  ██  ██     ██    ██   ██ ██ ████   ██ ██  ██  ██      ██   ██
██████    ████      ██    ███████ ██ ██ ██  ██ █████   █████   ██████
██         ██       ██    ██   ██ ██ ██  ██ ██ ██  ██  ██      ██   ██
██         ██       ██    ██   ██ ██ ██   ████ ██   ██ ███████ ██   ██
"""


def _step_banner(ctx: _WizardContext) -> StepResult:
    """Print the Pythinker ASCII banner + tagline."""
    from pythinker.cli import onboard as _onboard

    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    if cols >= 60:
        for line in _BANNER.strip("\n").splitlines():
            _onboard.console.print(line)
        _onboard.console.print("\n                       🐍 PYTHINKER 🐍\n")

    from pythinker import __version__

    _onboard.console.print(f"🐍 Pythinker {__version__}")
    _onboard.console.print("   Personal AI agent framework. ~2 minutes.\n")
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
    _onboard.console.print("  pythinker agent       interactive chat")
    _onboard.console.print("  pythinker gateway     start channels + API")
    _onboard.console.print("  pythinker doctor      verify your setup anytime\n")
    return StepResult(status="continue")
