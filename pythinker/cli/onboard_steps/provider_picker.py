"""Provider picker, auth-method picker, and run-auth steps."""

from __future__ import annotations

import os
import sys

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_provider_picker(ctx: _WizardContext) -> StepResult:
    """Step 7 — pick the LLM provider from the registry.

    Records the choice on `ctx.auth` so subsequent steps (auth-method picker,
    run-auth) can dispatch on it. The actual mutation of the config schema
    happens in Task 17 (run-auth) once the credential is in hand.
    """
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack

    if ctx.non_interactive:
        if ctx.auth in (None, "", "skip"):
            ctx.auth = ctx.auth or "skip"
            return StepResult(status="continue")
        # auth already set explicitly — keep it.
        return StepResult(status="continue")

    options = _onboard._build_provider_options()
    options.insert(0, ("__back__", "Back", "Return to the previous step"))
    chosen = clack.select(
        "Model/auth provider",
        options=options,
        default="openai_codex",
        searchable=True,
    )
    if chosen == "__back__":
        return StepResult(status="back")
    ctx.auth = chosen
    _onboard._emit_docs_link("provider")
    return StepResult(status="continue")


def _step_auth_method_picker(ctx: _WizardContext) -> StepResult:
    """Step 8 — pick the auth method for the chosen provider.

    Skipped when:
      - ctx.auth is None or "skip"
      - The provider's spec.auth_methods is empty (API-key-only providers)

    Auto-picks (no prompt) when:
      - spec.auth_methods has exactly one entry — emits a status line so the
        user sees what got chosen.
      - non_interactive mode — respects ctx.auth_method if set, else first entry.

    Otherwise prompts via clack.select with the methods + a "Back" option.
    """
    from pythinker.cli.onboard_views import clack
    from pythinker.providers.registry import PROVIDERS

    if ctx.auth is None or ctx.auth == "skip":
        return StepResult(status="skip")

    spec = next((s for s in PROVIDERS if s.name == ctx.auth), None)
    if spec is None or not spec.auth_methods:
        return StepResult(status="skip")

    if len(spec.auth_methods) == 1:
        ctx.auth_method = spec.auth_methods[0].id
        clack.print_status(f"Auth method: {spec.auth_methods[0].display}")
        clack.bar_break()
        return StepResult(status="continue")

    if ctx.non_interactive:
        ctx.auth_method = ctx.auth_method or spec.auth_methods[0].id
        return StepResult(status="continue")

    options: list[tuple[str, str, str]] = [
        (m.id, m.display, m.hint) for m in spec.auth_methods
    ]
    options.insert(0, ("__back__", "Back", "Return to provider picker"))

    chosen = clack.select(
        f"{spec.display_name} auth method",
        options=options,
        default=spec.auth_methods[0].id,
    )
    if chosen == "__back__":
        # Real back-nav now: orchestrator pops the history stack and re-runs
        # the provider picker, so the user can pick a different provider.
        return StepResult(status="back")

    ctx.auth_method = chosen
    return StepResult(status="continue")


def _step_run_auth(ctx: _WizardContext) -> StepResult:
    """Step 9 — execute the chosen auth method.

    For OAuth providers (browser-login): calls _login_via_oauth_remote.
    For API-key providers: prompts for the key with env-var indirection
    (writes ${VAR_NAME} when the env var is set, literal otherwise).
    """
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack
    from pythinker.providers.registry import PROVIDERS

    if ctx.auth is None or ctx.auth == "skip":
        return StepResult(status="skip")

    # Normalize provider name: convert hyphens to underscores for registry lookup
    normalized_name = ctx.auth.replace("-", "_") if ctx.auth else None
    spec = next(
        (s for s in PROVIDERS if s.name == normalized_name or s.name == ctx.auth),
        None,
    )
    if spec is None:
        return StepResult(status="skip")

    method = ctx.auth_method or (
        spec.auth_methods[0].id if spec.auth_methods else "api-key"
    )

    # Re-encountering an already-authenticated provider: ask before silently
    # re-running the auth flow. Mirrors pythinker's promptConfiguredAction
    # for the auth surface (Phase 1 task 4). The check intentionally inspects
    # only credentials *already saved in the draft* (api_key set, or OAuth
    # token file present) — env-var-only detection is handled by the existing
    # "Use {env_key} env var (currently set)?" confirm further down, so we
    # don't double-prompt on first-run env-var pickup. Suppressed in
    # non-interactive mode so CI/headless runs default to "update" semantics.
    if not ctx.non_interactive:
        already_authed = False
        try:
            provider_cfg = getattr(ctx.draft.providers, normalized_name, None)
            if spec.is_oauth:
                from pythinker.auth import credential_source

                if provider_cfg is not None:
                    already_authed = credential_source(spec, provider_cfg) == "oauth"
            else:
                already_authed = bool(getattr(provider_cfg, "api_key", "") or "")
        except Exception:  # noqa: BLE001
            already_authed = False
        if already_authed:
            action = _onboard._prompt_configured_action(
                f"{spec.display_name} auth",
                supports_disable=False,  # API keys: clearing is a one-line edit; OAuth: separate logout flow.
                update_text="Re-authenticate",
                skip_text="Skip (keep current credential)",
            )
            if action == "skip":
                clack.bar_break()
                return StepResult(status="continue")
            # action == "update" → fall through to the normal auth path.

    if method == "browser-login":
        prog = clack.progress(f"Awaiting browser login for {spec.display_name}")
        try:
            _onboard._login_via_oauth_remote(spec.name)
            prog.stop(success_label=f"{spec.display_name} OAuth complete")
            clack.bar_break()
            return StepResult(status="continue")
        except Exception as exc:  # noqa: BLE001
            prog.stop(success_label="")  # silent stop; actionable panel follows
            from pythinker.cli.onboard_views.errors import render_actionable

            render_actionable(
                what=f"Browser-login for {spec.display_name} failed: {exc}",
                why="Without a credential we cannot continue — the wizard will abort.",
                how=(
                    f"Re-run `pythinker onboard` and retry, or use an API key flow if "
                    f"{spec.display_name} supports one."
                ),
            )
            return StepResult(status="abort", message=f"oauth failed: {exc}")

    # API-key path. Check env var first.
    env_value = os.environ.get(spec.env_key) if spec.env_key else None

    if env_value and not ctx.non_interactive:
        use_env = clack.confirm(
            f"Use {spec.env_key} env var (currently set)?",
            default=True,
        )
        if use_env:
            _onboard._set_provider_api_key(ctx.draft, ctx.auth, f"${{{spec.env_key}}}")
            return StepResult(status="continue")

    if ctx.non_interactive:
        if not env_value:
            sys.stderr.write(
                f"--auth {ctx.auth} requires {spec.env_key} env var to be set\n"
            )
            sys.exit(1)
        _onboard._set_provider_api_key(ctx.draft, ctx.auth, f"${{{spec.env_key}}}")
        return StepResult(status="continue")

    pasted = clack.text(f"Paste your {spec.display_name} API key:")
    if not pasted.strip():
        return StepResult(status="abort", message="no api key provided")
    _onboard._set_provider_api_key(ctx.draft, ctx.auth, pasted.strip())
    return StepResult(status="continue")
