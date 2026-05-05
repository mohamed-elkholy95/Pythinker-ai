"""Security disclaimer step for the onboarding wizard."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_security_disclaimer(ctx: _WizardContext) -> StepResult:
    """Step 3 — security disclaimer + ack confirm."""
    from pythinker.cli.onboard_views import risk_ack

    if ctx.use_existing:
        return StepResult(status="skip")
    accepted = risk_ack.show_and_confirm(
        yes_security=ctx.yes_security,
        non_interactive=ctx.non_interactive,
    )
    if not accepted:
        return StepResult(status="abort", message="security disclaimer not accepted")
    return StepResult(status="continue")
