"""Default-model picker + workspace steps."""

from __future__ import annotations

from pathlib import Path

from pythinker.cli.onboard_options import (
    _model_belongs_to_provider,
    _resolve_model_route_hint,
)
from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_default_model(ctx: _WizardContext) -> StepResult:
    """Step 10 — pick the default model.

    * One single picker with all candidate models inlined as top-level
      options, plus ``Keep current`` and ``Enter model manually`` at the
      top. The user picks the actual model in one step, no meta-menu.
    * The initial highlight defaults to ``Keep current`` when the carried-
      over ``defaults.model`` belongs to the just-selected provider.
      Otherwise it jumps to the first recommended model for the new
      provider — so a user who just authed Codex sees the cursor on a
      codex model, not on the stale MiniMax one.
    * Each row carries a hint: ``recommended``, ``current``, the route
      hint (e.g. ``ChatGPT OAuth route``), or ``current (not in catalog)``
      for the user's existing model when it's outside the curated list.
    """
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack

    if ctx.auth is None or ctx.auth == "skip":
        return StepResult(status="skip")

    if ctx.non_interactive:
        return StepResult(status="continue")

    keep_key = _onboard._KEEP_KEY
    manual_key = _onboard._MANUAL_KEY
    back_key = _onboard._BACK_KEY

    provider_label = ctx.auth
    current = ctx.draft.agents.defaults.model or ""
    suggestions = _onboard.get_model_suggestions("", provider=ctx.auth) or []
    keep_compatible = bool(current) and _model_belongs_to_provider(current, ctx.auth)
    route_hint = _resolve_model_route_hint(ctx.auth)

    options: list[tuple[str, str, str]] = []
    options.append((back_key, "Back", "Return to the previous step"))
    if current:
        keep_label = f"Keep current ({current})"
        keep_hint = "" if keep_compatible else f"warning: not an {provider_label} model"
        options.append((keep_key, keep_label, keep_hint))
    options.append((manual_key, "Enter model manually", ""))

    seen: set[str] = set()
    for i, model_id in enumerate(suggestions):
        if model_id in seen:
            continue
        seen.add(model_id)
        hints: list[str] = []
        if i == 0:
            hints.append("recommended")
        if model_id == current:
            hints.append("current")
        if route_hint:
            hints.append(route_hint)
        options.append((model_id, model_id, " · ".join(hints)))

    # Surface the user's existing model even if it's outside the curated list,
    # so "current (not in catalog)" stays selectable rather than vanishing.
    if current and current not in seen and not keep_compatible:
        options.append((current, current, "current (not in catalog)"))

    if keep_compatible:
        default_id = keep_key
    elif suggestions:
        default_id = suggestions[0]
    elif current:
        default_id = keep_key
    else:
        default_id = manual_key

    title = f"Default model (provider: {provider_label})" if provider_label else "Default model"
    picked = clack.select(title, options=options, default=default_id, searchable=True)

    if picked == back_key:
        return StepResult(status="back")
    if picked == keep_key:
        _onboard._emit_docs_link("model")
        return StepResult(status="continue")
    if picked == manual_key:
        seed = current if keep_compatible else ""
        entered = clack.text("Model id:", default=seed)
        if entered.strip():
            ctx.draft.agents.defaults.model = entered.strip()
        _onboard._emit_docs_link("model")
        return StepResult(status="continue")

    ctx.draft.agents.defaults.model = picked
    _onboard._emit_docs_link("model")
    return StepResult(status="continue")


_WORKSPACE_MARKERS = ("MEMORY.md", "SOUL.md", "USER.md", "history.jsonl", "skills")


def _looks_like_pythinker_workspace(path: Path) -> bool:
    """Return True if ``path`` already contains Pythinker artifacts.

    Used to disambiguate between "fresh empty dir" and "user is reusing an
    existing workspace" so the wizard doesn't silently drop them into a
    populated dir without confirmation.
    """
    if not path.is_dir():
        return False
    return any((path / marker).exists() for marker in _WORKSPACE_MARKERS)


def _step_workspace(ctx: _WizardContext) -> StepResult:
    """Step 11 — workspace directory: mkdir, verify writable, re-prompt on failure."""
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack

    default = (
        ctx.workspace_override
        or ctx.draft.agents.defaults.workspace
        or "~/.pythinker/workspace"
    )

    while True:
        if ctx.non_interactive:
            entered = default
        else:
            entered = clack.text("Workspace directory:", default=default)
        path = Path(entered).expanduser()
        already_populated = _looks_like_pythinker_workspace(path)
        if already_populated and not ctx.non_interactive:
            action = _onboard._prompt_configured_action(
                f"Workspace {path}",
                supports_disable=False,
                update_text="Use this existing workspace",
                skip_text="Pick a different directory",
            )
            if action == "skip":
                continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doctor-probe"
            probe.write_text("x")
            probe.unlink()
            ctx.draft.agents.defaults.workspace = str(path)
            if not ctx.non_interactive:
                _onboard._emit_docs_link("workspace")
            return StepResult(status="continue")
        except (PermissionError, OSError) as exc:
            if ctx.non_interactive:
                return StepResult(
                    status="abort",
                    message=f"workspace not writable: {exc}",
                )
            clack.print_status(f"✗ {exc}")
            # Loop and re-prompt.
