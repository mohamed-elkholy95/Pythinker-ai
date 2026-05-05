"""Pre-save summary + save-config step."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_summary_confirm(ctx: _WizardContext) -> StepResult:
    """Step 14 — pre-save summary + Save / Skip decision.

    Performs the deferred reset rename atomically with the save when
    `ctx.reset_pending` is set. The destructive credential/session deletes
    happened immediately at step 4 (Task 12), so step 14 only handles the
    config.json → config.json.bak.<ts> rename.
    """
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack
    from pythinker.cli.onboard_views.summary import render_pre_save, render_pre_save_diff

    render_pre_save(ctx.draft)

    # Diff against the on-disk version when there is one — gives the user a
    # last-chance audit of exactly what's about to change. Mirrors pythinker's
    # pre-save diff panel (Phase 1 task 6). Best-effort: any IO/parse failure
    # silently degrades to the no-diff path (we never block save on it).
    try:
        existing_path = _onboard.get_config_path()
        if existing_path.exists():
            old_cfg = _onboard.load_config(existing_path)
            render_pre_save_diff(old_cfg, ctx.draft)
    except Exception:  # noqa: BLE001
        pass

    if ctx.non_interactive:
        chosen = "save"
    else:
        chosen = clack.select(
            "Save?",
            options=[
                ("save", "Save and exit", ""),
                ("skip", "Skip — discard changes", ""),
            ],
            default="save",
        )

    if chosen == "skip":
        return StepResult(status="abort", message="user skipped at summary")

    cfg_path = _onboard.get_config_path()
    old_sha = (
        hashlib.sha256(cfg_path.read_bytes()).hexdigest()[:12]
        if cfg_path.exists()
        else None
    )
    if ctx.reset_pending and cfg_path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        cfg_path.rename(cfg_path.with_name(f"config.json.bak.{ts}"))

    try:
        _onboard.save_config(ctx.draft, cfg_path)
    except OSError as exc:
        from pythinker.cli.onboard_views.errors import render_actionable

        render_actionable(
            what=f"Could not write config to {cfg_path}: {exc}",
            why="The wizard reached the save step but the filesystem refused the write.",
            how=(
                f"Check that {cfg_path.parent} exists and is writable, then re-run "
                "`pythinker onboard`. Common fixes: `mkdir -p` the parent, fix permissions, "
                "or pass `--config <other-path>` to land elsewhere."
            ),
        )
        return StepResult(status="abort", message=f"config save failed: {exc}")
    # User walked all the way through and confirmed save — clear the "use existing"
    # flag so _run_linear_wizard's final-return override doesn't report
    # should_save=False and trigger commands.py's "Configuration discarded" footer.
    ctx.use_existing = False
    new_sha = (
        hashlib.sha256(cfg_path.read_bytes()).hexdigest()[:12]
        if cfg_path.exists()
        else None
    )
    if new_sha is None:
        clack.print_status(f"Saved {cfg_path}")
    elif old_sha is not None:
        if old_sha == new_sha:
            # Identical bytes before/after = the wizard re-validated and
            # rewrote the same config. The user almost certainly intends
            # "I edited something" — surfacing "no fields changed" lets
            # them tell at a glance whether their save was a real edit
            # or a Keep-current / silent-discard no-op (the latter is the
            # symptom that triggered the Esc-saves-edits investigation
            # earlier in the session — guard the regression).
            clack.print_status(
                f"Updated {cfg_path} (sha256 {old_sha} — no fields changed)"
            )
        else:
            clack.print_status(f"Updated {cfg_path} (sha256 {old_sha} -> {new_sha})")
    else:
        clack.print_status(f"Saved {cfg_path} (sha256 {new_sha})")
    clack.bar_break()
    return StepResult(status="continue")
