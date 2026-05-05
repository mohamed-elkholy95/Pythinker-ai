"""Existing-config detection step for the onboarding wizard."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext
from pythinker.config.schema import Config


def _step_existing_config(ctx: _WizardContext) -> StepResult:
    """Step 4 — detect existing config, offer Use/Update/Reset."""
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack
    from pythinker.cli.onboard_views import reset as _reset_views
    from pythinker.cli.onboard_views import summary as _summary_views

    cfg_path = _onboard.get_config_path()
    if not cfg_path.exists():
        return StepResult(status="skip")

    on_disk = _onboard.load_config()
    ctx.started_from_existing = True

    if ctx.use_existing:
        ctx.draft = on_disk
        return StepResult(status="continue", next_step="_step_outro")

    _summary_views.render_existing_summary(on_disk)

    choice = clack.select(
        "What would you like to do?",
        options=[
            ("use-existing", "Use existing", "Load current config; refresh new schema fields."),
            ("update", "Update", "Walk the wizard; edit only what differs."),
            ("reset", "Reset", "Back up current config, start from defaults."),
        ],
        default="use-existing",
    )

    if choice == "use-existing":
        ctx.draft = on_disk
        ctx.use_existing = True
        return StepResult(status="continue", next_step="_step_outro")

    if choice == "update":
        ctx.draft = on_disk
        return StepResult(status="continue")

    # choice == "reset"
    scope = _reset_views.prompt_scope()
    if not _reset_views.confirm_typed():
        return StepResult(status="abort", message="reset not confirmed")

    _reset_views.apply_immediate(scope)
    ctx.reset_pending = True
    ctx.reset_scope = scope
    ctx.draft = Config()
    return StepResult(status="continue")
