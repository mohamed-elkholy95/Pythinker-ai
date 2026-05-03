"""Interactive onboarding questionnaire for pythinker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import types
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, NamedTuple, get_args, get_origin

import httpx

try:
    import questionary
except ModuleNotFoundError:  # pragma: no cover - exercised in environments without wizard deps
    questionary = None
from loguru import logger
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pythinker.agent.tools.web import WebSearchTool
from pythinker.cli.models import (
    RECOMMENDED_BY_PROVIDER,
    format_token_count,
    get_model_context_limit,
    get_model_suggestions,
)
from pythinker.config.loader import get_config_path, load_config, save_config
from pythinker.config.schema import (
    Config,
    WebSearchConfig,
    WebSearchProviderConfig,
)

if TYPE_CHECKING:
    from pythinker.providers.registry import ProviderSpec

console = Console()


@dataclass
class OnboardResult:
    """Result of an onboarding session."""

    config: Config
    should_save: bool


@dataclass(frozen=True)
class StepResult:
    """Result of a single wizard step.

    ``status`` values:

    - ``"continue"`` — proceed to the next step (or to ``next_step`` if set).
    - ``"skip"`` — same as continue; semantic marker that the step had nothing
      to do (e.g. step gated on QuickStart but flow is Manual).
    - ``"abort"`` — stop the wizard, do not save. ``message`` is shown to the
      user via ``clack.abort``.
    - ``"back"`` — request the orchestrator to re-run the previous step.
      Used by Task 5 (back-navigation across steps); the Task 1 orchestrator
      treats this the same as ``"continue"`` so steps can start emitting it
      ahead of the orchestrator support landing.

    ``next_step`` is reserved for the back-nav work — when non-empty, the
    orchestrator jumps to the named step instead of the default next-in-list.
    Today it's accepted but unused.
    """

    status: Literal["continue", "skip", "abort", "back"]
    message: str = ""
    next_step: str = ""


@dataclass
class _WizardContext:
    """Shared state threaded through every wizard step.

    Field groupings (kept stable so phases 2–4 can extend without churn):

    - **flow control**: ``flow``, ``non_interactive``, ``yes_security``,
      ``use_existing``, ``use_existing_committed``, ``started_from_existing``.
    - **provider/auth choice**: ``auth``, ``auth_method``.
    - **runtime intents**: ``start_gateway``, ``skip_gateway``,
      ``gateway_started``, ``open_webui``.
    - **reset / migration**: ``reset_pending``, ``reset_scope``.
    - **path overrides**: ``workspace_override``.
    - **deferred side-effects** (``deferred``): callables a step registers to
      run once the orchestrator finishes successfully — e.g. opening a
      browser tab after the gateway is up. Drained by ``_run_linear_wizard``
      after the last step returns; exceptions are logged, not raised.

    Carries the linear-wizard state across steps at the data-shape level.
    """

    draft: Config
    flow: str = "manual"
    non_interactive: bool = False
    yes_security: bool = False
    auth: str | None = None
    auth_method: str | None = None
    start_gateway: bool | None = None
    skip_gateway: bool = False
    started_from_existing: bool = False
    use_existing: bool = False
    use_existing_committed: bool = False
    reset_pending: bool = False
    reset_scope: object | None = None
    workspace_override: str | None = None
    open_webui: bool = False
    gateway_started: bool = False
    deferred: list[Callable[[], None]] = field(default_factory=list)

    def register_deferred(self, fn: Callable[[], None]) -> None:
        """Register a fire-and-forget callback to run after the orchestrator
        finishes successfully (i.e. no abort, no exception). Used by steps
        that need work to land *after* a later step — e.g. opening the WebUI
        browser tab once the gateway has bound its port. Order is registration
        order; an exception in one deferred does not stop the others."""
        self.deferred.append(fn)


_WIZARD_STEPS: list[Callable[[_WizardContext], StepResult]] = []


# Per-step documentation pointers. Each step that calls ``_emit_docs_link``
# uses one of these keys to print a dim "Docs: …" footer after the main
# prompt — every section ends with a docs URL the user can paste into a
# browser.
_DOCS_BASE = "https://github.com/mohamed-elkholy95/pythinker/blob/main/docs"
_DOCS_LINKS: dict[str, str] = {
    "provider": f"{_DOCS_BASE}/configuration.md#providers",
    "channels": f"{_DOCS_BASE}/chat-apps.md",
    "search": f"{_DOCS_BASE}/configuration.md#search",
    "model": f"{_DOCS_BASE}/configuration.md#models",
    "workspace": f"{_DOCS_BASE}/configuration.md#workspace",
    "security": f"{_DOCS_BASE}/security.md",
}


def _emit_docs_link(key: str) -> None:
    """Print a one-line dim ``Docs: <url>`` footer for the given step key.
    Silently no-ops when the key isn't in ``_DOCS_LINKS`` (the calling step
    is then untouched — failing closed is fine for a UX accessory)."""
    from pythinker.cli.onboard_views import clack

    url = _DOCS_LINKS.get(key)
    if url:
        clack.print_status(f"Docs: {url}")


def _run_linear_wizard(ctx: _WizardContext) -> OnboardResult:
    """Run each registered step. Supports back-navigation.

    Walker semantics (mirrors pythinker's wizard session walker):

    - ``continue`` — push current step onto the history stack, advance.
    - ``skip``     — advance without pushing (a skipped step has nothing
                     to "go back to").
    - ``back``     — pop the history stack and re-run that step. When the
                     stack is empty we are at the beginning, so back is a
                     no-op (the banner step then advances normally).
    - ``next_step`` — explicit jump by step function name. Pushes the
                     current step onto history first so a subsequent
                     ``back`` returns here.
    - ``abort`` — close the wizard, do not save.

    Caught exceptions:
      - ``clack.WizardCancelled`` (Ctrl-C/Esc inside a prompt) → abort cleanly.
      - Any other ``Exception`` → log and abort. Draft is never saved.
    """
    from pythinker.cli.onboard_views import clack

    history: list[int] = []
    idx = 0
    n = len(_WIZARD_STEPS)
    while idx < n:
        step = _WIZARD_STEPS[idx]
        try:
            result = step(ctx)
        except clack.WizardCancelled:
            clack.abort(f"cancelled at {step.__name__}")
            return OnboardResult(config=ctx.draft, should_save=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Onboard step {} failed", step.__name__)
            clack.abort(f"{step.__name__} failed: {exc}")
            return OnboardResult(config=ctx.draft, should_save=False)

        if result.status == "abort":
            clack.abort(result.message or "user aborted")
            return OnboardResult(config=ctx.draft, should_save=False)

        if result.status == "back":
            if history:
                idx = history.pop()
                continue
            # At the very first step — back has nowhere to go. Re-run the
            # step instead of advancing so the user can re-make the choice.
            continue

        if result.next_step:
            target_idx = next(
                (i for i, s in enumerate(_WIZARD_STEPS) if s.__name__ == result.next_step),
                None,
            )
            if target_idx is not None:
                history.append(idx)
                idx = target_idx
                continue

        # continue / skip both advance; only continue records history.
        if result.status == "continue":
            history.append(idx)
        idx += 1

    # Drain any side-effects registered via ``ctx.register_deferred``. These
    # run for both should_save=True and use_existing paths since both finish
    # the wizard successfully.
    for fn in ctx.deferred:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("Deferred onboard side-effect failed")

    if ctx.use_existing:
        return OnboardResult(config=ctx.draft, should_save=False)

    return OnboardResult(config=ctx.draft, should_save=True)


# --- Banner + Intro + Outro Steps ---

_BANNER = r"""
██████  ██    ██ ████████ ██   ██ ██ ███    ██ ██   ██ ███████ ██████
██   ██  ██  ██     ██    ██   ██ ██ ████   ██ ██  ██  ██      ██   ██
██████    ████      ██    ███████ ██ ██ ██  ██ █████   █████   ██████
██         ██       ██    ██   ██ ██ ██  ██ ██ ██  ██  ██      ██   ██
██         ██       ██    ██   ██ ██ ██   ████ ██   ██ ███████ ██   ██
"""


def _step_banner(ctx: _WizardContext) -> StepResult:
    """Print the Pythinker ASCII banner + tagline."""
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    if cols >= 60:
        for line in _BANNER.strip("\n").splitlines():
            console.print(line)
        console.print("\n                       🐍 PYTHINKER 🐍\n")

    from pythinker import __version__

    console.print(f"🐍 Pythinker {__version__}")
    console.print("   Personal AI agent framework. ~2 minutes.\n")
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
    from pythinker.cli.onboard_views import clack

    clack.outro("🐍 Pythinker is ready.")
    console.print("\nNext:")
    console.print("  pythinker agent       interactive chat")
    console.print("  pythinker gateway     start channels + API")
    console.print("  pythinker doctor      verify your setup anytime\n")
    return StepResult(status="continue")


_WIZARD_STEPS.extend([_step_banner, _step_intro])
# _step_outro and _step_start_gateway are registered together at the
# bottom of the file: outro first (its message must survive the exec),
# then start_gateway which replaces the wizard process when accepted.


# --- Step 3: Security Disclaimer ---


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


_WIZARD_STEPS.append(_step_security_disclaimer)


# --- Step 4: Existing Config Detection ---


def _step_existing_config(ctx: _WizardContext) -> StepResult:
    """Step 4 — detect existing config, offer Use/Update/Reset."""
    from pythinker.cli.onboard_views import clack
    from pythinker.cli.onboard_views import reset as _reset_views
    from pythinker.cli.onboard_views import summary as _summary_views

    cfg_path = get_config_path()
    if not cfg_path.exists():
        return StepResult(status="skip")

    on_disk = load_config()
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


_WIZARD_STEPS.append(_step_existing_config)


# --- Step 5: Flow Picker (QuickStart vs Manual) ---


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


_WIZARD_STEPS.append(_step_flow_picker)


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


_WIZARD_STEPS.append(_step_quickstart_summary)


def _format_provider_hint(spec: "ProviderSpec") -> str:
    """Format a one-line hint for a provider row.

    Surfaces the auth style (OAuth / gateway / local / direct / API key),
    the relevant env var or default endpoint, and a ``signup ↗`` marker
    when the provider exposes a signup URL the wizard can deep-link to.
    ``clack.select`` only takes one hint string, so we join the segments
    with ' · '.
    """
    parts: list[str] = []
    if getattr(spec, "is_oauth", False):
        # OAuth wins the badge: no env var matters once browser-login is the path.
        if spec.name == "openai_codex":
            parts.append("OAuth · ChatGPT login")
        elif spec.name == "github_copilot":
            parts.append("OAuth · GitHub login")
        else:
            parts.append("OAuth")
    elif getattr(spec, "is_direct", False):
        parts.append("Direct · OpenAI-compatible endpoint")
    elif getattr(spec, "is_local", False):
        parts.append("Local")
        if getattr(spec, "default_api_base", ""):
            parts.append(spec.default_api_base)
    elif getattr(spec, "is_gateway", False):
        parts.append("Gateway")
        if getattr(spec, "default_api_base", ""):
            parts.append(spec.default_api_base)
    elif getattr(spec, "env_key", ""):
        parts.append(f"API key · {spec.env_key}")
    if getattr(spec, "signup_url", ""):
        parts.append("signup ↗")
    return " · ".join(parts)


def _provider_picker_bucket(spec: object) -> int:
    """Sort key controlling the visual grouping of provider rows.

    Lower = earlier. OAuth first (one-click), then direct/standard providers
    (most users), then gateways, then local installs, then anything else.
    Mirrors pythinker's ``sortFlowContributionsByLabel`` after stable bucket
    grouping.
    """
    if getattr(spec, "is_oauth", False):
        return 0
    if getattr(spec, "is_direct", False):
        return 1
    if getattr(spec, "is_gateway", False):
        return 3
    if getattr(spec, "is_local", False):
        return 4
    return 2  # standard "API key" providers


def _build_provider_options() -> list[tuple[str, str, str]]:
    """Build the provider-picker options list with pythinker-style decorated hints.

    Bucketed (OAuth → Direct → Standard → Gateway → Local) then alphabetically
    sorted within each bucket for stable presentation. The hint column carries
    auth style, endpoint, and a signup marker so the user can tell at a glance
    *what* they're picking, not just *who*.
    """
    from pythinker.providers.registry import PROVIDERS

    decorated = [
        (
            _provider_picker_bucket(spec),
            (spec.display_name or spec.name).lower(),
            (spec.name, spec.display_name or spec.name, _format_provider_hint(spec)),
        )
        for spec in PROVIDERS
    ]
    decorated.sort(key=lambda row: (row[0], row[1]))
    options = [row[2] for row in decorated]
    options.append(("skip", "Skip", "Configure later in config.json."))
    return options


def _step_provider_picker(ctx: _WizardContext) -> StepResult:
    """Step 7 — pick the LLM provider from the registry.

    Records the choice on `ctx.auth` so subsequent steps (auth-method picker,
    run-auth) can dispatch on it. The actual mutation of the config schema
    happens in Task 17 (run-auth) once the credential is in hand.
    """
    from pythinker.cli.onboard_views import clack

    if ctx.non_interactive:
        if ctx.auth in (None, "", "skip"):
            ctx.auth = ctx.auth or "skip"
            return StepResult(status="continue")
        # auth already set explicitly — keep it.
        return StepResult(status="continue")

    options = _build_provider_options()
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
    _emit_docs_link("provider")
    return StepResult(status="continue")


_WIZARD_STEPS.append(_step_provider_picker)


# --- Step 8: Auth Method Picker ---


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


_WIZARD_STEPS.append(_step_auth_method_picker)


def _login_via_oauth_remote(provider_name: str) -> None:
    """Bridge to existing OAuth login handlers.

    Looks up `_LOGIN_HANDLERS` from `pythinker.cli.commands` and calls the
    matching handler.  Each registered handler emits an SSH-awareness hint via
    ``pythinker.auth.oauth_remote.run_oauth_with_hint`` before opening the
    browser, so SSH/headless users see the paste-fallback option upfront.

    ``login_oauth_interactive`` (oauth_cli_kit) races a local callback server
    against a stdin paste prompt, so the paste path works without any extra
    timeout wrapper — the hint makes it discoverable.
    """
    from pythinker.cli.commands import _LOGIN_HANDLERS

    handler = _LOGIN_HANDLERS.get(provider_name)
    if handler is None:
        raise RuntimeError(f"No OAuth handler registered for {provider_name}")
    handler()


def _set_provider_api_key(cfg: Config, provider_name: str, value: str) -> None:
    """Set cfg.providers.<provider_name>.api_key.

    Hyphenated provider names map to underscored attribute names.
    Silently no-ops if the schema doesn't know this provider.
    """
    attr = provider_name.replace("-", "_")
    pc = getattr(cfg.providers, attr, None)
    if pc is None:
        return
    pc.api_key = value


def _step_run_auth(ctx: _WizardContext) -> StepResult:
    """Step 9 — execute the chosen auth method.

    For OAuth providers (browser-login): calls _login_via_oauth_remote.
    For API-key providers: prompts for the key with env-var indirection
    (writes ${VAR_NAME} when the env var is set, literal otherwise).
    """
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
            action = _prompt_configured_action(
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
            _login_via_oauth_remote(spec.name)
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
            _set_provider_api_key(ctx.draft, ctx.auth, f"${{{spec.env_key}}}")
            return StepResult(status="continue")

    if ctx.non_interactive:
        if not env_value:
            sys.stderr.write(
                f"--auth {ctx.auth} requires {spec.env_key} env var to be set\n"
            )
            sys.exit(1)
        _set_provider_api_key(ctx.draft, ctx.auth, f"${{{spec.env_key}}}")
        return StepResult(status="continue")

    pasted = clack.text(f"Paste your {spec.display_name} API key:")
    if not pasted.strip():
        return StepResult(status="abort", message="no api key provided")
    _set_provider_api_key(ctx.draft, ctx.auth, pasted.strip())
    return StepResult(status="continue")


_WIZARD_STEPS.append(_step_run_auth)


# --- Step 10: Default Model Picker ---


def _normalize_provider_id(provider: str) -> str:
    """Normalize provider ids so ``openai-codex`` and ``openai_codex`` collide."""
    return (provider or "").strip().lower().replace("-", "_")


def _model_belongs_to_provider(model: str, provider: str) -> bool:
    """Best-effort check: does ``model`` look like it belongs to ``provider``?

    Used to spot "user kept their old MiniMax model id but just switched the
    provider to OpenAI Codex" so we don't preselect an incompatible default.
    Errs conservative — false negatives only cost one extra picker step.
    """
    from pythinker.providers.registry import PROVIDERS

    if not model or not provider:
        return False

    norm_provider = _normalize_provider_id(provider)
    needle = model.lower()
    spec = next(
        (p for p in PROVIDERS if _normalize_provider_id(p.name) == norm_provider),
        None,
    )
    if spec is not None:
        if any(kw and kw in needle for kw in spec.keywords):
            return True
    recommended = RECOMMENDED_BY_PROVIDER.get(norm_provider) or RECOMMENDED_BY_PROVIDER.get(
        provider, ()
    )
    if model in recommended:
        return True
    if "/" in needle and spec is not None:
        prefix = needle.split("/", 1)[0].replace("_", "-")
        if prefix == _normalize_provider_id(spec.name).replace("_", "-"):
            return True
    return False


def _resolve_model_route_hint(provider: str) -> str:
    """Per-provider one-word route label, mirrors pythinker's ``resolveModelRouteHint``."""
    norm = _normalize_provider_id(provider)
    if norm == "openai":
        return "API key route"
    if norm == "openai_codex":
        return "ChatGPT OAuth route"
    if norm == "github_copilot":
        return "GitHub OAuth route"
    return ""


_KEEP_KEY = "__keep__"
_MANUAL_KEY = "__manual__"
_BACK_KEY = "__back__"


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
    from pythinker.cli.onboard_views import clack

    if ctx.auth is None or ctx.auth == "skip":
        return StepResult(status="skip")

    if ctx.non_interactive:
        return StepResult(status="continue")

    provider_label = ctx.auth
    current = ctx.draft.agents.defaults.model or ""
    suggestions = get_model_suggestions("", provider=ctx.auth) or []
    keep_compatible = bool(current) and _model_belongs_to_provider(current, ctx.auth)
    route_hint = _resolve_model_route_hint(ctx.auth)

    options: list[tuple[str, str, str]] = []
    options.append((_BACK_KEY, "Back", "Return to the previous step"))
    if current:
        keep_label = f"Keep current ({current})"
        keep_hint = "" if keep_compatible else f"warning: not an {provider_label} model"
        options.append((_KEEP_KEY, keep_label, keep_hint))
    options.append((_MANUAL_KEY, "Enter model manually", ""))

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
        default_id = _KEEP_KEY
    elif suggestions:
        default_id = suggestions[0]
    elif current:
        default_id = _KEEP_KEY
    else:
        default_id = _MANUAL_KEY

    title = f"Default model (provider: {provider_label})" if provider_label else "Default model"
    picked = clack.select(title, options=options, default=default_id, searchable=True)

    if picked == _BACK_KEY:
        return StepResult(status="back")
    if picked == _KEEP_KEY:
        return StepResult(status="continue")
    if picked == _MANUAL_KEY:
        seed = current if keep_compatible else ""
        entered = clack.text("Model id:", default=seed)
        if entered.strip():
            ctx.draft.agents.defaults.model = entered.strip()
        return StepResult(status="continue")

    ctx.draft.agents.defaults.model = picked
    return StepResult(status="continue")


_WIZARD_STEPS.append(_step_default_model)


def _step_workspace(ctx: _WizardContext) -> StepResult:
    """Step 11 — workspace directory: mkdir, verify writable, re-prompt on failure."""
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
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doctor-probe"
            probe.write_text("x")
            probe.unlink()
            ctx.draft.agents.defaults.workspace = str(path)
            return StepResult(status="continue")
        except (PermissionError, OSError) as exc:
            if ctx.non_interactive:
                return StepResult(
                    status="abort",
                    message=f"workspace not writable: {exc}",
                )
            clack.print_status(f"✗ {exc}")
            # Loop and re-prompt.


_WIZARD_STEPS.append(_step_workspace)


def _channel_is_enabled(cfg: Config, channel_name: str) -> bool:
    """True if ``cfg.channels.<channel_name>`` exists and has ``enabled=True``."""
    ch = getattr(cfg.channels, channel_name, None)
    if ch is None:
        return False
    if isinstance(ch, dict):
        return bool(ch.get("enabled", False))
    return bool(getattr(ch, "enabled", False))


def _set_channel_enabled(cfg: Config, channel_name: str, enabled: bool) -> None:
    """Set ``cfg.channels.<channel_name>.enabled`` in-place, working for both
    dict-shaped and pydantic-model-shaped channel sub-configs."""
    ch = getattr(cfg.channels, channel_name, None)
    if ch is None:
        return
    if isinstance(ch, dict):
        ch["enabled"] = enabled
    else:
        try:
            setattr(ch, "enabled", enabled)
        except (AttributeError, TypeError):
            # Frozen / unsupported model — caller's job to surface this elsewhere.
            pass


def _prompt_configured_action(
    label: str,
    *,
    supports_disable: bool = True,
    update_text: str = "Modify settings",
    disable_text: str = "Disable (keeps config)",
    skip_text: str = "Skip (leave as-is)",
) -> str:
    """Action picker for an already-configured target.

    Used by every onboard step that may re-encounter an already-configured
    target — channels, providers, search backends, workspace dirs — so the
    user is never silently dropped back into the per-field editor when they
    might have only wanted to disable or skip.

    Returns one of: ``"update"`` (re-run the editor / reconfigure), ``"disable"``
    (turn the feature off but keep its config — only when ``supports_disable``
    is True), ``"skip"`` (no-op, leave as-is). The ``delete`` action pythinker
    exposes is intentionally omitted in pythinker — clearing a pydantic
    sub-block can't reliably round-trip without losing schema-resident
    defaults, so we leave clearing to the editor itself.
    """
    from pythinker.cli.onboard_views import clack

    options: list[tuple[str, str, str]] = [("update", update_text, "")]
    if supports_disable:
        options.append(("disable", disable_text, ""))
    options.append(("skip", skip_text, ""))

    return clack.select(
        f"{label} already configured. What do you want to do?",
        options=options,
        default="update",
    )


def _prompt_configured_channel_action(label: str) -> str:
    """Channel-specific shim around :func:`_prompt_configured_action`. Retained
    so existing tests and call sites continue to work unchanged."""
    return _prompt_configured_action(label, supports_disable=True)


def _step_channels(ctx: _WizardContext) -> StepResult:
    """Step 12 — channel picker loop.

    Skipped in QuickStart. Manual flow:
      1) Show "How channels work" panel.
      2) Loop: pick a channel from the dynamically-discovered registry
         (telegram, discord, slack, email, matrix, msteams, whatsapp, websocket),
         show its instructions if available, then run the full per-field
         pydantic walker (`_configure_channel`) on its config.
      3) "Done" returns to the main wizard flow.

    Channels come from `pythinker.channels.registry` so the wizard never
    lags the codebase, and the per-channel editor covers every field the
    channel exposes — not just the auth token.
    """
    from pythinker.cli.onboard_views import clack
    from pythinker.cli.onboard_views.panels import CHANNEL_INSTRUCTIONS, CHANNELS_INTRO

    if ctx.flow != "manual":
        return StepResult(status="skip")

    clack.note("How channels work", CHANNELS_INTRO)
    clack.bar_break()

    channel_names = _get_channel_names()  # {registry_key: display_name}
    if not channel_names:
        clack.print_status("No channels available — skipping channel setup.")
        return StepResult(status="continue")

    while True:
        options: list[tuple[str, str, str]] = []
        for name, display in channel_names.items():
            ch = getattr(ctx.draft.channels, name, None)
            enabled = bool(ch) and (
                ch.get("enabled") if isinstance(ch, dict) else getattr(ch, "enabled", False)
            )
            hint = "configured" if enabled else ""
            options.append((name, display, hint))
        options.append(("__done__", "Done — continue setup", ""))

        try:
            picked = clack.select(
                "Configure a channel",
                options=options,
                default="__done__",
                searchable=True,
            )
        except clack.WizardCancelled:
            break

        if picked == "__done__":
            break

        # When a channel is already enabled, ask the user what they want to do
        # before silently dropping into the editor — they may have just wanted
        # to disable it, or to leave it alone after re-checking.
        if _channel_is_enabled(ctx.draft, picked):
            display = channel_names.get(picked, picked.title())
            action = _prompt_configured_channel_action(display)
            if action == "skip":
                clack.bar_break()
                continue
            if action == "disable":
                _set_channel_enabled(ctx.draft, picked, False)
                clack.print_status(f"{display} disabled (config kept).")
                clack.bar_break()
                continue
            # action == "update" → fall through to the editor below.

        instr = CHANNEL_INSTRUCTIONS.get(picked)
        if instr:
            clack.note(f"{channel_names.get(picked, picked.title())} setup", instr)
            clack.bar_break()

        # Reuse the existing per-field pydantic walker; it covers
        # every field on the channel's config class (token, allowlists, polling
        # intervals, webhook URLs, …) rather than just the auth token.
        try:
            _configure_channel(ctx.draft, picked)
        except KeyboardInterrupt:
            clack.print_status(f"{channel_names.get(picked, picked)} edit cancelled.")
            continue

        clack.bar_break()

    _emit_docs_link("channels")
    return StepResult(status="continue")


_WIZARD_STEPS.append(_step_channels)


def _step_search_provider(ctx: _WizardContext) -> StepResult:
    """Step 13 — search provider picker (Manual only).

    QuickStart defers search-provider config. Manual mode shows a single-select
    over known providers; chosen provider's API key is read either as inline
    paste or as ${VAR} env-var indirection (heuristic: looks like ENV_VAR_NAME).

    Re-run behavior: when a draft already carries a search provider with an
    api_key, the picker is pre-defaulted to that provider, "(configured)" hints
    are shown next to providers that already have a credential, and an explicit
    "Keep current" option is offered so users don't have to re-paste a key just
    to walk through the wizard. Without this the previous flow always reset to
    Tavily-default and re-prompted, which surfaced as "I pasted the key but it
    didn't save" (it had — the user just hit Enter on the next re-paste).
    """
    from pythinker.cli.onboard_views import clack

    if ctx.flow != "manual":
        return StepResult(status="skip")

    current_provider, configured_providers = _read_search_provider_state(ctx.draft)

    def _label(opt_id: str, base: str, hint: str) -> tuple[str, str]:
        if opt_id in configured_providers:
            base += "  (configured)"
        return base, hint

    options: list[tuple[str, str, str]] = []
    for opt_id, base, hint in (
        ("tavily", "Tavily Search", "TAVILY_API_KEY · structured results"),
        ("brave", "Brave Search", "BRAVE_API_KEY"),
        ("perplexity", "Perplexity Search", "PERPLEXITY_API_KEY"),
    ):
        display, h = _label(opt_id, base, hint)
        options.append((opt_id, display, h))
    if current_provider in configured_providers:
        options.insert(0, ("__keep__", f"Keep current ({current_provider})", "no changes"))
    options.append(("skip", "Skip for now", ""))

    default = "__keep__" if current_provider in configured_providers else (
        current_provider if current_provider else "tavily"
    )
    chosen = clack.select(
        "Search provider", options=options, default=default, searchable=True
    )

    if chosen in ("skip", "__keep__"):
        return StepResult(status="continue")

    pasted = clack.text(f"{chosen} API key (or env var name like TAVILY_API_KEY):")
    pasted = pasted.strip()
    if not pasted:
        # Empty input + the user already had a key for this provider = keep it,
        # but still flip the active provider to their new pick so the agent uses it.
        if chosen in configured_providers:
            _activate_search_provider(ctx.draft, chosen)
            clack.print_status(f"Search provider: {chosen} (kept existing key)")
        return StepResult(status="continue")

    if not pasted.startswith("${") and pasted.isupper() and "_" in pasted:
        # Heuristic: looks like an env var name; wrap in ${} for loader expansion.
        pasted = f"${{{pasted}}}"

    _write_search_provider_key(ctx.draft, chosen, pasted)
    clack.print_status(f"Search provider: {chosen}")
    _emit_docs_link("search")
    return StepResult(status="continue")


def _read_search_provider_state(cfg: Config) -> tuple[str | None, set[str]]:
    """Return (active_provider_name, set_of_provider_names_with_api_key).

    Tolerates schema mismatches by walking with getattr/hasattr — same defensive
    pattern as ``_write_search_provider_key`` so this never raises mid-wizard.
    """
    tools = getattr(cfg, "tools", None)
    web = getattr(tools, "web", None) if tools else None
    search = getattr(web, "search", None) if web else None
    if search is None:
        return None, set()

    active = getattr(search, "provider", None) or None
    providers = getattr(search, "providers", None) or {}
    configured = {
        name
        for name, slot in providers.items()
        if getattr(slot, "api_key", "") or getattr(slot, "apiKey", "")
    }
    return active, configured


def _activate_search_provider(cfg: Config, provider_name: str) -> None:
    """Flip the active search provider without touching credentials."""
    tools = getattr(cfg, "tools", None)
    web = getattr(tools, "web", None) if tools else None
    search = getattr(web, "search", None) if web else None
    if search is not None and hasattr(search, "provider"):
        search.provider = provider_name


def _write_search_provider_key(cfg: Config, provider_name: str, value: str) -> None:
    """Write the chosen search provider's API key into the config draft.

    Uses defensive getattr/hasattr to avoid crashing on schema mismatches.
    Adapt based on what the schema actually exposes.
    """
    # Schema path: cfg.tools.web.search.providers[provider_name].api_key
    search_cfg = getattr(cfg, "tools", None)
    if search_cfg is None:
        return

    search_cfg = getattr(search_cfg, "web", None)
    if search_cfg is None:
        return

    search_cfg = getattr(search_cfg, "search", None)
    if search_cfg is None:
        return

    providers = getattr(search_cfg, "providers", None)
    if providers is None:
        return

    # Create or update the provider config
    if provider_name not in providers:
        from pythinker.config.schema import WebSearchProviderConfig

        providers[provider_name] = WebSearchProviderConfig()

    provider_cfg = providers[provider_name]
    if hasattr(provider_cfg, "api_key"):
        provider_cfg.api_key = value

    # Activate the picked provider — without this, the agent keeps using whatever
    # was previously selected (default: duckduckgo) even after the user gives a key.
    if hasattr(search_cfg, "provider"):
        search_cfg.provider = provider_name


_WIZARD_STEPS.append(_step_search_provider)


def _step_summary_confirm(ctx: _WizardContext) -> StepResult:
    """Step 14 — pre-save summary + Save / Skip decision.

    Performs the deferred reset rename atomically with the save when
    `ctx.reset_pending` is set. The destructive credential/session deletes
    happened immediately at step 4 (Task 12), so step 14 only handles the
    config.json → config.json.bak.<ts> rename.
    """
    from pythinker.cli.onboard_views import clack
    from pythinker.cli.onboard_views.summary import render_pre_save, render_pre_save_diff

    render_pre_save(ctx.draft)

    # Diff against the on-disk version when there is one — gives the user a
    # last-chance audit of exactly what's about to change. Mirrors pythinker's
    # pre-save diff panel (Phase 1 task 6). Best-effort: any IO/parse failure
    # silently degrades to the no-diff path (we never block save on it).
    try:
        existing_path = get_config_path()
        if existing_path.exists():
            old_cfg = load_config(existing_path)
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

    cfg_path = get_config_path()
    old_sha = (
        hashlib.sha256(cfg_path.read_bytes()).hexdigest()[:12]
        if cfg_path.exists()
        else None
    )
    if ctx.reset_pending and cfg_path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        cfg_path.rename(cfg_path.with_name(f"config.json.bak.{ts}"))

    try:
        save_config(ctx.draft, cfg_path)
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


_WIZARD_STEPS.append(_step_summary_confirm)


# --- Step 15: Post-save health check ---


def _check_gateway_port_free(host: str, port: int) -> tuple[str, str]:
    """Best-effort port preflight that does NOT exit. Returns ``(status, detail)``
    where status is ``"ok"`` / ``"warn"`` / ``"error"``. Used by the post-save
    health check so a port conflict shows up as a warning (config is fine,
    the user just needs to free the port before starting the gateway), not
    as a hard failure on the wizard."""
    import errno
    import socket

    bind_host = host or "127.0.0.1"
    fam = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(fam, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, port))
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            return ("warn", f"{bind_host}:{port} already in use")
        if exc.errno == errno.EACCES:
            return ("warn", f"{bind_host}:{port} permission denied")
        return ("warn", f"bind failed ({exc.errno})")
    sock.close()
    return ("ok", f"{bind_host}:{port} free")


def _step_post_save_health(ctx: _WizardContext) -> StepResult:
    """Step 15 — green/yellow/red health check on the just-saved config.

    Inlines the relevant subset of ``pythinker doctor`` so the wizard ends
    on a confidence-building summary rather than dropping the user back at
    the shell.

    Skipped when the user discarded changes (``use_existing`` and no save).
    Provider-ping is intentionally NOT included by default — making a
    network call as the last step of onboarding hides cost and would
    slow headless installs. Add it later behind an opt-in flag.
    """
    from pythinker.cli.doctor import (
        _check_default_model,
        _check_default_provider_auth,
        _check_workspace,
    )
    from pythinker.cli.onboard_views import clack

    if ctx.use_existing:
        return StepResult(status="skip")

    glyph = {"ok": "✓", "warn": "⚠", "error": "✗"}

    def _emit(status: str, label: str, detail: str = "", fix: str = "") -> None:
        line = f"{glyph.get(status, '?')} {label}"
        if detail:
            line += f": {detail}"
        clack.print_status(line)
        if fix and status != "ok":
            clack.print_status(f"  Fix: {fix}")

    clack.print_status("Health check:")
    try:
        ws = _check_workspace()
        _emit(ws.status, ws.label, ws.detail, ws.fix)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Workspace", f"check skipped ({exc})")

    try:
        model = _check_default_model()
        _emit(model.status, model.label, model.detail, model.fix)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Default model", f"check skipped ({exc})")

    try:
        for auth_result in _check_default_provider_auth():
            _emit(auth_result.status, auth_result.label, auth_result.detail, auth_result.fix)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Provider auth", f"check skipped ({exc})")

    try:
        port_status, port_detail = _check_gateway_port_free(
            ctx.draft.gateway.host, ctx.draft.gateway.port
        )
        _emit(port_status, "Gateway port", port_detail)
    except Exception as exc:  # noqa: BLE001
        _emit("warn", "Gateway port", f"check skipped ({exc})")

    clack.bar_break()
    return StepResult(status="continue")


_WIZARD_STEPS.append(_step_post_save_health)


# --- Step 16: Start Gateway (Optional) ---


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


# Outro before start_gateway: start_gateway execs into the gateway and never
# returns, so any post-exec step (including the outro's next-steps message)
# would silently be dropped if the order were reversed.
_WIZARD_STEPS.extend([_step_outro, _step_start_gateway])


# --- Field Hints for Select Fields ---
# Maps field names to (choices, hint_text)
# To add a new select field with hints, add an entry:
#   "field_name": (["choice1", "choice2", ...], "hint text for the field")
_SELECT_FIELD_HINTS: dict[str, tuple[list[str], str]] = {
    "reasoning_effort": (
        ["low", "medium", "high"],
        "low / medium / high - enables LLM thinking mode",
    ),
}

# --- Key Bindings for Navigation ---

_BACK_PRESSED = object()  # Sentinel value for back navigation


def _get_questionary():
    """Return questionary or raise a clear error when wizard deps are unavailable."""
    if questionary is None:
        raise RuntimeError(
            "Interactive onboarding requires the optional 'questionary' dependency. "
            "Install project dependencies and rerun with 'pythinker onboard'."
        )
    return questionary


def _select_with_back(
    prompt: str, choices: list[str], default: str | None = None
) -> str | None | object:
    """Select with Escape/Left arrow support for going back.

    Args:
        prompt: The prompt text to display.
        choices: List of choices to select from. Must not be empty.
        default: The default choice to pre-select. If not in choices, first item is used.

    Returns:
        _BACK_PRESSED sentinel if user pressed Escape or Left arrow
        The selected choice string if user confirmed
        None if user cancelled (Ctrl+C)
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    # Validate choices
    if not choices:
        logger.warning("Empty choices list provided to _select_with_back")
        return None

    # Find default index
    selected_index = 0
    if default and default in choices:
        selected_index = choices.index(default)

    # State holder for the result
    state: dict[str, str | None | object] = {"result": None}

    # Build menu items (uses closure over selected_index)
    def get_menu_text():
        items = []
        for i, choice in enumerate(choices):
            if i == selected_index:
                # Wrap the active row with connected dashes (┄) so it reads as
                # a hollow striped highlight instead of a filled background.
                items.append(("class:cursor-row", f"┄ {choice} ┄┄┄\n"))
            else:
                items.append(("", f"  {choice}\n"))
        return items

    # Create layout
    menu_control = FormattedTextControl(get_menu_text)
    menu_window = Window(content=menu_control, height=len(choices))

    prompt_control = FormattedTextControl(lambda: [("class:question", f"◆  {prompt}")])
    # always_hide_cursor: prompt_toolkit otherwise parks its block cursor on the
    # first cell of the focused control, which lands on top of the ● glyph and
    # reads as a hover/highlight artefact.
    prompt_window = Window(content=prompt_control, height=1, always_hide_cursor=True)

    layout = Layout(HSplit([prompt_window, menu_window]))

    # Key bindings
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(choices)
        event.app.invalidate()

    @bindings.add(Keys.Down)
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(choices)
        event.app.invalidate()

    @bindings.add(Keys.Enter)
    def _enter(event):
        state["result"] = choices[selected_index]
        event.app.exit()

    @bindings.add("escape")
    def _escape(event):
        state["result"] = _BACK_PRESSED
        event.app.exit()

    @bindings.add(Keys.Left)
    def _left(event):
        state["result"] = _BACK_PRESSED
        event.app.exit()

    @bindings.add(Keys.ControlC)
    def _ctrl_c(event):
        state["result"] = None
        event.app.exit()

    # Style. Use a custom class name (not "selected") because prompt_toolkit's
    # built-in `selected` class adds reverse-video, which renders as the green
    # filled box. `noinherit` makes sure no parent rule re-introduces a bg.
    style = Style.from_dict({
        "cursor-row": "fg:ansigreen bold noinherit",
        "question": "fg:cyan",
    })

    app = Application(layout=layout, key_bindings=bindings, style=style)
    try:
        app.run()
    except Exception:
        logger.exception("Error in select prompt")
        return None

    return state["result"]

# --- Type Introspection ---


class FieldTypeInfo(NamedTuple):
    """Result of field type introspection."""

    type_name: str
    inner_type: Any


def _get_field_type_info(field_info) -> FieldTypeInfo:
    """Extract field type info from Pydantic field."""
    annotation = field_info.annotation
    if annotation is None:
        return FieldTypeInfo("str", None)

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is types.UnionType:
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]
            origin = get_origin(annotation)
            args = get_args(annotation)

    simple_types: dict[type, str] = {bool: "bool", int: "int", float: "float"}

    if origin is list or (hasattr(origin, "__name__") and origin.__name__ == "List"):
        return FieldTypeInfo("list", args[0] if args else str)
    if origin is dict or (hasattr(origin, "__name__") and origin.__name__ == "Dict"):
        return FieldTypeInfo("dict", None)
    for py_type, name in simple_types.items():
        if annotation is py_type:
            return FieldTypeInfo(name, None)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return FieldTypeInfo("model", annotation)
    if origin is Literal:
        return FieldTypeInfo("literal", list(args))
    return FieldTypeInfo("str", None)


def _get_field_display_name(field_key: str, field_info) -> str:
    """Get display name for a field."""
    if field_info and field_info.description:
        return field_info.description
    name = field_key
    suffix_map = {
        "_s": " (seconds)",
        "_ms": " (ms)",
        "_url": " URL",
        "_path": " Path",
        "_id": " ID",
        "_key": " Key",
        "_token": " Token",
    }
    for suffix, replacement in suffix_map.items():
        if name.endswith(suffix):
            name = name[: -len(suffix)] + replacement
            break
    return name.replace("_", " ").title()


# --- Sensitive Field Masking ---

_SENSITIVE_KEYWORDS = frozenset({"api_key", "token", "secret", "password", "credentials"})


def _is_sensitive_field(field_name: str) -> bool:
    """Check if a field name indicates sensitive content."""
    return any(kw in field_name.lower() for kw in _SENSITIVE_KEYWORDS)


def _mask_value(value: str) -> str:
    """Mask a sensitive value, showing only the last 4 characters."""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


def _mask_token(value: str | None) -> str:
    """Mask an API token / key for display. Don't leak short values.

    Long enough (≥ 12 chars) → first 4 + '...' + last 4.
    Anything shorter (or None / empty) → '***'.
    Used by the env-var detection step in _configure_provider so we can
    prompt 'Use this key?' without echoing the whole secret.
    """
    if not value or len(value) < 12:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


# --- Value Formatting ---


def _format_value(value: Any, rich: bool = True, field_name: str = "") -> str:
    """Single recursive entry point for safe value display. Handles any depth."""
    if value is None or value == "" or value == {} or value == []:
        return "[dim]not set[/dim]" if rich else "[not set]"
    if _is_sensitive_field(field_name) and isinstance(value, str):
        masked = _mask_value(value)
        return f"[dim]{masked}[/dim]" if rich else masked
    if isinstance(value, BaseModel):
        parts = []
        for fname, _finfo in type(value).model_fields.items():
            fval = getattr(value, fname, None)
            formatted = _format_value(fval, rich=False, field_name=fname)
            if formatted != "[not set]":
                parts.append(f"{fname}={formatted}")
        return ", ".join(parts) if parts else ("[dim]not set[/dim]" if rich else "[not set]")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        # Handle dicts containing BaseModel instances
        parts = []
        for k, v in value.items():
            formatted = _format_value(v, rich=False, field_name=str(k))
            parts.append(f"{k}: {formatted}")
        return ", ".join(parts) if parts else ("[dim]not set[/dim]" if rich else "[not set]")
    return str(value)


def _format_value_for_input(value: Any, field_type: str) -> str:
    """Format a value for use as input default."""
    if value is None or value == "":
        return ""
    if field_type == "list" and isinstance(value, list):
        return ",".join(str(v) for v in value)
    if field_type == "dict" and isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def _validate_field_constraint(value: Any, field_info) -> str | None:
    """Validate a value against Pydantic Field constraints.

    Returns an error message string if validation fails, None if valid.
    Uses attribute-based detection to handle Pydantic v2 internal types.
    """
    if field_info is None or not hasattr(field_info, "metadata"):
        return None

    for m in field_info.metadata:
        if hasattr(m, "ge") and isinstance(value, (int, float)):
            if value < m.ge:
                return f"Value must be >= {m.ge}"
        if hasattr(m, "gt") and isinstance(value, (int, float)):
            if value <= m.gt:
                return f"Value must be > {m.gt}"
        if hasattr(m, "le") and isinstance(value, (int, float)):
            if value > m.le:
                return f"Value must be <= {m.le}"
        if hasattr(m, "lt") and isinstance(value, (int, float)):
            if value >= m.lt:
                return f"Value must be < {m.lt}"
        if hasattr(m, "min_length") and hasattr(value, "__len__"):
            if len(value) < m.min_length:
                return f"Length must be >= {m.min_length}"
        if hasattr(m, "max_length") and hasattr(value, "__len__"):
            if len(value) > m.max_length:
                return f"Length must be <= {m.max_length}"

    return None


def _get_constraint_hint(field_info) -> str:
    """Derive a human-readable constraint hint from field metadata.

    Returns a string like "(0-10)" or "(>= 0)" to append to field display names.
    """
    if field_info is None or not hasattr(field_info, "metadata"):
        return ""

    ge_val = None
    le_val = None
    for m in field_info.metadata:
        if hasattr(m, "ge"):
            ge_val = m.ge
        if hasattr(m, "le"):
            le_val = m.le

    if ge_val is not None and le_val is not None:
        return f" ({ge_val}-{le_val})"
    if ge_val is not None:
        return f" (>= {ge_val})"
    if le_val is not None:
        return f" (<= {le_val})"
    return ""


# --- Rich UI Components ---


def _show_config_panel(display_name: str, model: BaseModel, fields: list) -> None:
    """Display current configuration as a rich table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    for fname, field_info in fields:
        value = getattr(model, fname, None)
        display = _get_field_display_name(fname, field_info)
        formatted = _format_value(value, rich=True, field_name=fname)
        table.add_row(display, formatted)

    console.print(Panel(table, title=f"[bold]{display_name}[/bold]", border_style="blue"))


def _show_section_header(title: str, subtitle: str = "") -> None:
    """Display a section header."""
    console.print()
    if subtitle:
        console.print(
            Panel(f"[dim]{subtitle}[/dim]", title=f"[bold]{title}[/bold]", border_style="blue")
        )
    else:
        console.print(Panel("", title=f"[bold]{title}[/bold]", border_style="blue"))


# --- Input Handlers ---


def _input_bool(display_name: str, current: bool | None) -> bool | None:
    """Get boolean input via confirm dialog."""
    return _get_questionary().confirm(
        display_name,
        default=bool(current) if current is not None else False,
    ).ask()


def _input_text(display_name: str, current: Any, field_type: str, field_info=None) -> Any:
    """Get text input and parse based on field type."""
    default = _format_value_for_input(current, field_type)

    value = _get_questionary().text(f"{display_name}:", default=default).ask()

    if value is None or value == "":
        return None

    if field_type == "int":
        try:
            parsed = int(value)
        except ValueError:
            console.print("[yellow]! Invalid number format, value not saved[/yellow]")
            return None
        if field_info:
            error = _validate_field_constraint(parsed, field_info)
            if error:
                console.print(f"[yellow]! {error}, value not saved[/yellow]")
                return None
        return parsed
    elif field_type == "float":
        try:
            parsed = float(value)
        except ValueError:
            console.print("[yellow]! Invalid number format, value not saved[/yellow]")
            return None
        if field_info:
            error = _validate_field_constraint(parsed, field_info)
            if error:
                console.print(f"[yellow]! {error}, value not saved[/yellow]")
                return None
        return parsed
    elif field_type == "list":
        return [v.strip() for v in value.split(",") if v.strip()]
    elif field_type == "dict":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            console.print("[yellow]! Invalid JSON format, value not saved[/yellow]")
            return None

    return value


def _input_with_existing(
    display_name: str, current: Any, field_type: str, field_info=None
) -> Any:
    """Handle input with 'keep existing' option for non-empty values."""
    has_existing = current is not None and current != "" and current != {} and current != []

    if has_existing and not isinstance(current, list):
        choice = _get_questionary().select(
            display_name,
            choices=["Enter new value", "Keep existing value"],
            default="Keep existing value",
        ).ask()
        if choice == "Keep existing value" or choice is None:
            return None

    return _input_text(display_name, current, field_type, field_info=field_info)


# --- Pydantic Model Configuration ---


def _get_current_provider(model: BaseModel) -> str:
    """Get the current provider setting from a model (if available)."""
    if hasattr(model, "provider"):
        return getattr(model, "provider", "auto") or "auto"
    return "auto"


def _input_model_with_autocomplete(
    display_name: str, current: Any, provider: str
) -> str | None:
    """Get model input with autocomplete suggestions."""
    from prompt_toolkit.completion import Completer, Completion

    default = str(current) if current else ""

    class DynamicModelCompleter(Completer):
        """Completer that dynamically fetches model suggestions."""

        def __init__(self, provider_name: str):
            self.provider = provider_name

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            suggestions = get_model_suggestions(text, provider=self.provider, limit=50)
            for model in suggestions:
                # Skip if model doesn't contain the typed text
                if text.lower() not in model.lower():
                    continue
                yield Completion(
                    model,
                    start_position=-len(text),
                    display=model,
                )

    value = _get_questionary().autocomplete(
        f"{display_name}:",
        choices=[""],  # Placeholder, actual completions from completer
        completer=DynamicModelCompleter(provider),
        default=default,
        qmark="●",
    ).ask()

    return value if value else None


def _input_context_window_with_recommendation(
    display_name: str, current: Any, model_obj: BaseModel
) -> int | None:
    """Get context window input with option to fetch recommended value."""
    current_val = current if current else ""

    choices = ["Enter new value"]
    if current_val:
        choices.append("Keep existing value")
    choices.append("[?] Get recommended value")

    choice = _get_questionary().select(
        display_name,
        choices=choices,
        default="Enter new value",
    ).ask()

    if choice is None:
        return None

    if choice == "Keep existing value":
        return None

    if choice == "[?] Get recommended value":
        # Get the model name from the model object
        model_name = getattr(model_obj, "model", None)
        if not model_name:
            console.print("[yellow]! Please configure the model field first[/yellow]")
            return None

        provider = _get_current_provider(model_obj)
        context_limit = get_model_context_limit(model_name, provider)

        if context_limit:
            console.print(
                f"[green]+ Recommended context window: "
                f"{format_token_count(context_limit)} tokens[/green]"
            )
            return context_limit
        else:
            console.print("[yellow]! Could not fetch model info, please enter manually[/yellow]")
            # Fall through to manual input

    # Manual input
    value = _get_questionary().text(
        f"{display_name}:",
        default=str(current_val) if current_val else "",
    ).ask()

    if value is None or value == "":
        return None

    try:
        return int(value)
    except ValueError:
        console.print("[yellow]! Invalid number format, value not saved[/yellow]")
        return None


def _handle_model_field(
    working_model: BaseModel, field_name: str, field_display: str, current_value: Any
) -> None:
    """Handle the 'model' field with autocomplete and context-window auto-fill."""
    provider = _get_current_provider(working_model)
    new_value = _input_model_with_autocomplete(field_display, current_value, provider)
    if new_value is not None and new_value != current_value:
        setattr(working_model, field_name, new_value)
        _try_auto_fill_context_window(working_model, new_value)


def _handle_context_window_field(
    working_model: BaseModel, field_name: str, field_display: str, current_value: Any
) -> None:
    """Handle context_window_tokens with recommendation lookup."""
    new_value = _input_context_window_with_recommendation(
        field_display, current_value, working_model
    )
    if new_value is not None:
        setattr(working_model, field_name, new_value)


_FIELD_HANDLERS: dict[str, Any] = {
    "model": _handle_model_field,
    "context_window_tokens": _handle_context_window_field,
}


def _configure_pydantic_model(
    model: BaseModel,
    display_name: str,
    *,
    skip_fields: set[str] | None = None,
) -> BaseModel | None:
    """Configure a Pydantic model interactively.

    Returns the updated ``working_model`` in three cases — ``[Done]`` Enter,
    Esc, and Left arrow — all treated as "save and back". Returns ``None``
    only when the user hits Ctrl-C and explicitly confirms the discard at
    the follow-up prompt; a clean Ctrl-C with no pending edits also returns
    ``None``. Callers should ``setattr(parent, field, returned_model)``
    whenever the return is non-None.

    Why Esc/Left save instead of discard: the original behavior silently
    threw away every field the user had just edited (token, model name,
    etc.), then the on-disk default-only block via the schema backfill made
    it look like the wizard had saved something. Save-on-back matches the
    instinct people bring from every other settings UI.
    """
    skip_fields = skip_fields or set()
    working_model = model.model_copy(deep=True)
    original_dump = model.model_dump()  # frozen baseline for "is dirty?" check

    fields = [
        (name, info)
        for name, info in type(working_model).model_fields.items()
        if name not in skip_fields
    ]
    if not fields:
        console.print(f"[dim]{display_name}: No configurable fields[/dim]")
        return working_model

    def _is_dirty() -> bool:
        return working_model.model_dump() != original_dump

    def _confirm_discard() -> bool:
        """Ask before throwing away pending edits. True = user confirmed discard."""
        from pythinker.cli.onboard_views import clack

        try:
            return clack.confirm(
                f"Discard your edits to {display_name}?",
                default=False,
            )
        except clack.WizardCancelled:
            # Second Ctrl-C inside the confirm itself = honor the discard intent.
            return True

    def get_choices() -> list[str]:
        items = []
        for fname, finfo in fields:
            value = getattr(working_model, fname, None)
            display = _get_field_display_name(fname, finfo)
            formatted = _format_value(value, rich=False, field_name=fname)
            items.append(f"{display}: {formatted}")
        return items + ["[Done]"]

    while True:
        console.clear()
        _show_config_panel(display_name, working_model, fields)
        choices = get_choices()
        answer = _select_with_back("Select field to configure:", choices)

        # Esc / Left arrow: "save and back" — preserves edits up to the caller.
        if answer is _BACK_PRESSED:
            return working_model
        # Ctrl-C: clean exit if nothing changed; otherwise prompt before discarding.
        if answer is None:
            if _is_dirty() and not _confirm_discard():
                return working_model
            return None
        if answer == "[Done]":
            return working_model

        field_idx = next((i for i, c in enumerate(choices) if c == answer), -1)
        if field_idx < 0 or field_idx >= len(fields):
            return None

        field_name, field_info = fields[field_idx]
        current_value = getattr(working_model, field_name, None)
        ftype = _get_field_type_info(field_info)
        field_display = (
            _get_field_display_name(field_name, field_info) + _get_constraint_hint(field_info)
        )

        # Nested Pydantic model - recurse
        if ftype.type_name == "model":
            nested = current_value
            created = nested is None
            if nested is None and ftype.inner_type:
                nested = ftype.inner_type()
            if nested and isinstance(nested, BaseModel):
                updated = _configure_pydantic_model(nested, field_display)
                if updated is not None:
                    setattr(working_model, field_name, updated)
                elif created:
                    setattr(working_model, field_name, None)
            continue

        # Registered special-field handlers
        handler = _FIELD_HANDLERS.get(field_name)
        if handler:
            handler(working_model, field_name, field_display, current_value)
            continue

        # Select fields with hints (e.g. reasoning_effort)
        if field_name in _SELECT_FIELD_HINTS:
            choices_list, hint = _SELECT_FIELD_HINTS[field_name]
            select_choices = choices_list + ["(clear/unset)"]
            console.print(f"[dim]  Hint: {hint}[/dim]")
            new_value = _select_with_back(
                field_display, select_choices, default=current_value or select_choices[0]
            )
            if new_value is _BACK_PRESSED:
                continue
            if new_value == "(clear/unset)":
                setattr(working_model, field_name, None)
            elif new_value is not None:
                setattr(working_model, field_name, new_value)
            continue

        # Generic field input
        if ftype.type_name == "literal" and ftype.inner_type:
            select_choices = [str(v) for v in ftype.inner_type]
            default_choice = (
                str(current_value) if current_value in ftype.inner_type else select_choices[0]
            )
            new_value = _select_with_back(field_display, select_choices, default=default_choice)
            if new_value is _BACK_PRESSED:
                continue
            if new_value is not None:
                setattr(working_model, field_name, new_value)
            continue
        if ftype.type_name == "bool":
            new_value = _input_bool(field_display, current_value)
        else:
            new_value = _input_with_existing(
                field_display, current_value, ftype.type_name, field_info=field_info
            )
        if new_value is not None:
            setattr(working_model, field_name, new_value)


def _try_auto_fill_context_window(model: BaseModel, new_model_name: str) -> None:
    """Try to auto-fill context_window_tokens if it's at default value.

    Note:
        This function imports AgentDefaults from pythinker.config.schema to get
        the default context_window_tokens value. If the schema changes, this
        coupling needs to be updated accordingly.
    """
    # Check if context_window_tokens field exists
    if not hasattr(model, "context_window_tokens"):
        return

    current_context = getattr(model, "context_window_tokens", None)

    # Check if current value is the default
    # We only auto-fill if the user hasn't changed it from default
    from pythinker.config.schema import AgentDefaults

    default_context = AgentDefaults.model_fields["context_window_tokens"].default

    if current_context != default_context:
        return  # User has customized it, don't override

    provider = _get_current_provider(model)
    context_limit = get_model_context_limit(new_model_name, provider)

    if context_limit:
        setattr(model, "context_window_tokens", context_limit)
        console.print(
            f"[green]+ Auto-filled context window: "
            f"{format_token_count(context_limit)} tokens[/green]"
        )
    else:
        console.print("[dim](i) Could not auto-fill context window (model not in database)[/dim]")


# --- Per-provider pre-key hooks ---------------------------------------------

# Region URLs used by MiniMax pre-key hook. Two regions, two flavors → 4 bases.
_MINIMAX_REGION_BASES = {
    ("Global (api.minimax.io)", "minimax"): "https://api.minimax.io/v1",
    ("Global (api.minimax.io)", "minimax_anthropic"): "https://api.minimax.io/anthropic",
    ("Mainland China (api.minimaxi.com)", "minimax"): "https://api.minimaxi.com/v1",
    ("Mainland China (api.minimaxi.com)", "minimax_anthropic"): "https://api.minimaxi.com/anthropic",
}

_MINIMAX_REGION_SIGNUP_URLS = {
    "Global (api.minimax.io)": "https://platform.minimax.io/user-center/payment/token-plan",
    "Mainland China (api.minimaxi.com)": "https://platform.minimaxi.com/user-center/payment/token-plan",
}


def _detect_minimax_region(api_base: str | None) -> str:
    """Infer the region picker default from an existing api_base."""
    if api_base and "minimaxi.com" in api_base:
        return "Mainland China (api.minimaxi.com)"
    return "Global (api.minimax.io)"


def _minimax_pre_key(provider_config: BaseModel, provider_name: str) -> str:
    """Pre-key hook for MiniMax: pick region, set api_base, return signup URL.

    Runs before the env-detect / browser-open ladder so Mainland users are
    sent to platform.minimaxi.com rather than platform.minimax.io.
    """
    choices = [
        "Global (api.minimax.io)",
        "Mainland China (api.minimaxi.com)",
    ]
    default = _detect_minimax_region(getattr(provider_config, "api_base", None))
    region = _select_with_back("MiniMax region:", choices, default=default)
    if region is _BACK_PRESSED or region is None:
        region = default

    base_key = (region, provider_name)
    if base_key in _MINIMAX_REGION_BASES:
        provider_config.api_base = _MINIMAX_REGION_BASES[base_key]

    return _MINIMAX_REGION_SIGNUP_URLS[region]


# Dispatch table: provider_name -> hook(provider_config, provider_name) -> signup_url.
# Hook may mutate provider_config (e.g. set api_base) and may return a
# signup_url override that the browser-open step will use instead of
# spec.signup_url. Returning "" leaves spec.signup_url in effect.
_PRE_KEY_HOOKS = {
    "minimax": _minimax_pre_key,
    "minimax_anthropic": _minimax_pre_key,
}


# --- Provider Configuration ---


def _no_provider_key_set(config: Config) -> bool:
    """True iff no registered provider has a non-empty api_key on `config`.

    Drives the first-run LLM-provider nudge in run_onboard. Considers EVERY
    provider — gateways (OpenRouter etc.) and local backends (Ollama etc.)
    are valid first-provider choices, so signup_url_required() is the wrong
    filter here.
    """
    from pythinker.providers.registry import PROVIDERS

    return not any(
        (getattr(getattr(config.providers, spec.name, None), "api_key", "") or "")
        for spec in PROVIDERS
    )


@lru_cache(maxsize=1)
def _get_provider_info() -> dict[str, tuple[str, bool, bool, str]]:
    """Get provider info from registry (cached)."""
    from pythinker.providers.registry import PROVIDERS

    # Include OAuth providers (openai-codex, github-copilot) in the picker.
    # _configure_provider routes them to the OAuth login flow instead of an
    # API-key prompt. Filtering them out hid the Codex / Copilot login paths
    # from users who only see the wizard — they had no way to discover that
    # `pythinker provider login openai-codex` exists.
    return {
        spec.name: (
            spec.display_name or spec.name,
            spec.is_gateway,
            spec.is_local,
            spec.default_api_base,
        )
        for spec in PROVIDERS
    }


def _get_provider_names() -> dict[str, str]:
    """Get provider display names."""
    info = _get_provider_info()
    return {name: data[0] for name, data in info.items() if name}


def _run_oauth_login(spec) -> None:
    """Trigger the OAuth login flow for an `is_oauth=True` ProviderSpec.

    Same logic as the `pythinker provider login <name>` CLI command, but
    callable from inside the wizard. Status is rendered through the clack
    timeline helpers so each event (✓ authenticated, ✗ failure, paste
    prompts) sits on the persistent ``│`` bar, keeping the diamond column
    aligned with the rest of the wizard.

    Implementation notes:

    * ``prompt_fn`` uses ``input()`` instead of a nested questionary
      session, because questionary acquires the TTY exclusively while a
      picker is open — a nested prompt session inside the OAuth callback
      conflicts and silently aborts, sending the user back to the picker
      with no error printed.
    * ``clack.pause()`` at the end keeps the success / failure message on
      screen until the user acknowledges it; the wizard's next
      ``console.clear()`` would otherwise wipe the OAuth result.
    """
    from pythinker.cli.onboard_views import clack

    label = spec.label

    def _prompt(s: str) -> str:
        # Bypass questionary inside the OAuth callback so we don't fight
        # the outer picker for the TTY. Plain input() is enough for
        # paste-back codes, and surfaces Ctrl-C / Ctrl-D cleanly.
        try:
            return input(f"{s}: ") if not s.endswith(":") else input(s + " ")
        except (EOFError, KeyboardInterrupt):
            return ""

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit import get_token, login_oauth_interactive
        except ImportError:
            clack.failure("oauth_cli_kit not installed.", "Run: pip install oauth-cli-kit")
            clack.pause()
            return

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if token and getattr(token, "access", None):
            account = getattr(token, "account_id", "") or ""
            clack.success(f"Already authenticated with {label}", account or None)
            clack.pause()
            return

        clack.print_status(f"Starting OAuth login for {label}...")
        try:
            token = login_oauth_interactive(
                print_fn=lambda s: clack.print_status(str(s)),
                prompt_fn=_prompt,
            )
        except KeyboardInterrupt:
            clack.failure("Login cancelled.")
            clack.pause()
            return
        except Exception as exc:
            clack.failure("Authentication failed.", str(exc))
            clack.pause()
            return

        if token and getattr(token, "access", None):
            account = getattr(token, "account_id", None) or "OpenAI"
            clack.success(f"Authenticated with {label}", account)
        else:
            clack.failure("Authentication failed (no token returned).")
        clack.pause()
        return

    if spec.name == "github_copilot":
        try:
            from pythinker.providers.github_copilot_provider import login_github_copilot
        except ImportError as exc:
            clack.failure(f"Cannot start {label} login.", str(exc))
            clack.pause()
            return

        clack.print_status(f"Starting {label} device flow...")
        try:
            token = login_github_copilot(
                print_fn=lambda s: clack.print_status(str(s)),
                prompt_fn=_prompt,
            )
        except KeyboardInterrupt:
            clack.failure("Login cancelled.")
            clack.pause()
            return
        except Exception as exc:
            clack.failure("Authentication failed.", str(exc))
            clack.pause()
            return

        account = getattr(token, "account_id", None) or "GitHub"
        clack.success(f"Authenticated with {label}", account)
        clack.pause()
        return

    clack.failure(f"OAuth login not implemented for {label}.")
    clack.pause()


def _configure_provider(config: Config, provider_name: str) -> None:
    """Configure a single LLM provider with optional auto-open + MiniMax follow-up.

    OAuth providers (openai-codex, github-copilot) bypass the API-key prompt
    entirely and dispatch to the OAuth login flow — same handlers that
    `pythinker provider login <name>` uses, so the wizard and CLI surface
    the same auth experience.
    """
    from pythinker.providers.registry import PROVIDERS, signup_url_required

    spec = next((s for s in PROVIDERS if s.name == provider_name), None)

    # OAuth providers (codex, copilot) don't have an api_key field to fill.
    # Hand off to the same login handler the CLI uses; treat success as the
    # provider being "configured" (the OAuth token is persisted by oauth_cli_kit
    # under platformdirs, not in config.json).
    if spec and spec.is_oauth:
        _run_oauth_login(spec)
        return

    provider_config = getattr(config.providers, provider_name, None)
    if provider_config is None:
        console.print(f"[red]Unknown provider: {provider_name}[/red]")
        return
    display_name = _get_provider_names().get(provider_name, provider_name)
    info = _get_provider_info()
    default_api_base = info.get(provider_name, (None, None, None, None))[3]

    if default_api_base and not provider_config.api_base:
        provider_config.api_base = default_api_base

    # --- Snapshot for MiniMax follow-up undo (Findings 5 + 7) ---------------
    # Always built (cheap: two model_copy() calls) so the post-walker
    # dispatch can rely on the snapshot unconditionally — Tasks 5-7 use it
    # to undo walker writes when the user picks a different flavor.
    prev_key = provider_config.api_key or ""
    provider_snapshot = {
        "minimax": config.providers.minimax.model_copy(deep=True),
        "minimax_anthropic": config.providers.minimax_anthropic.model_copy(deep=True),
    }

    # --- Generic auto-open ladder (runs only when api_key is empty) ---------
    if not provider_config.api_key:
        # 0. Per-provider pre-key hook (region pick, etc.)
        signup_url_override = ""
        hook = _PRE_KEY_HOOKS.get(provider_name)
        if hook:
            signup_url_override = hook(provider_config, provider_name=provider_name)

        # 1. Env-var detection
        env_value = os.environ.get(spec.env_key, "") if (spec and spec.env_key) else ""
        env_used = False
        if env_value:
            confirmed = _get_questionary().confirm(
                f"Found {spec.env_key} in environment ({_mask_token(env_value)}). "
                f"Use this key?",
                default=True,
            ).ask()
            if confirmed:
                provider_config.api_key = env_value
                env_used = True

        # 2. Browser open
        if not env_used:
            signup_url = signup_url_override or (
                spec.signup_url if (spec and signup_url_required(spec)) else ""
            )
            if signup_url:
                console.print(Panel(
                    f"[bold]{display_name}[/bold] needs an API key.\n"
                    f"Open [cyan]{signup_url}[/cyan] in your browser?"
                    + (
                        f"\n[dim]Learn more: {spec.docs_url}[/dim]"
                        if (spec and spec.docs_url) else ""
                    ),
                    title="API key",
                    border_style="cyan",
                ))
                if _get_questionary().confirm(
                    "Open browser now?", default=True,
                ).ask():
                    try:
                        webbrowser.open(signup_url)
                    except Exception as exc:  # noqa: BLE001 — never block onboarding
                        logger.debug("webbrowser.open({!r}) raised: {}", signup_url, exc)
                    console.print(f"[dim]URL: {signup_url}[/dim]")

    # --- Existing field walker ---------------------------------------------
    updated_provider = _configure_pydantic_model(
        provider_config,
        display_name,
    )
    if updated_provider is not None:
        setattr(config.providers, provider_name, updated_provider)

    # --- MiniMax-specific post-walk follow-up (Task 5+) ---------------------
    # Only MiniMax needs a post-walk follow-up; if a second provider arises,
    # mirror _PRE_KEY_HOOKS with a _POST_WALK_HOOKS dispatch table.
    if provider_name in {"minimax", "minimax_anthropic"}:
        _configure_minimax_followup(
            config, provider_name, prev_key, provider_snapshot,
        )


# Canonical region+flavor URLs used by the "stale config" predicate.
# Derived from _MINIMAX_REGION_BASES so the two cannot drift.
_MINIMAX_CANONICAL_BASES: dict[str, set[str]] = {}
for (_region, _pname), _url in _MINIMAX_REGION_BASES.items():
    _MINIMAX_CANONICAL_BASES.setdefault(_pname, set()).add(_url)
del _region, _pname, _url


def _minimax_followup_needs_offer(
    config: Config, provider_name: str
) -> bool:
    """Return True iff the existing MiniMax setup looks incomplete or stale."""
    other = "minimax_anthropic" if provider_name == "minimax" else "minimax"
    other_cfg = getattr(config.providers, other)
    this_cfg = getattr(config.providers, provider_name)
    counterpart_empty = not (other_cfg.api_key or "")
    base_off_canonical = (this_cfg.api_base or "") not in _MINIMAX_CANONICAL_BASES[provider_name]
    model_not_minimax = not (config.agents.defaults.model or "").startswith("MiniMax-")
    return counterpart_empty or base_off_canonical or model_not_minimax


def _minimax_followup_run_steps(
    config: Config,
    provider_name: str,
    provider_snapshot: dict,
) -> None:
    """Run region → flavor → plan-tier → validate. Each sub-step is its own
    function so tests can monkeypatch them independently."""
    region = _minimax_followup_region_step(config, provider_name)
    if provider_name == "minimax_anthropic":
        _warn_on_anthropic_env_overrides()
    validate_target = _minimax_followup_flavor_step(
        config, provider_name, provider_snapshot, region,
    )
    _minimax_followup_plan_tier_step(config, region)
    _minimax_followup_validate_step(config, validate_target)


def _warn_on_anthropic_env_overrides() -> None:
    """Surface ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL conflicts (opencode hint)."""
    leaks = [v for v in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(v)]
    if not leaks:
        return
    console.print(
        f"[yellow]Warning: {', '.join(leaks)} set in your environment. "
        f"It may override the MiniMax Anthropic endpoint at runtime — "
        f"consider unsetting it.[/yellow]"
    )


def _minimax_followup_region_step(config: Config, provider_name: str) -> str:
    """Pick (or reuse) the region. Returns the chosen region label."""
    this_cfg = getattr(config.providers, provider_name)
    current_base = this_cfg.api_base or ""
    if current_base in _MINIMAX_CANONICAL_BASES[provider_name]:
        # Pre-key hook (or prior setup) already set a canonical base;
        # don't re-prompt. Just return the implied region.
        return _detect_minimax_region(current_base)

    choices = ["Global (api.minimax.io)", "Mainland China (api.minimaxi.com)"]
    default = _detect_minimax_region(current_base)
    region = _select_with_back("MiniMax region:", choices, default=default)
    if region is _BACK_PRESSED or region is None:
        region = default
    base_key = (region, provider_name)
    if base_key in _MINIMAX_REGION_BASES:
        this_cfg.api_base = _MINIMAX_REGION_BASES[base_key]
    return region


_FLAVOR_OPENAI = "OpenAI-compatible (default — supports reasoning_split thinking)"
_FLAVOR_ANTHROPIC = "Anthropic-compatible (native thinking blocks visible in stream)"
_FLAVOR_BOTH = "Both (recommended — same key, both flavors)"


def _minimax_followup_flavor_step(
    config: Config, provider_name: str, provider_snapshot: dict, region: str,
) -> str:
    """Pick endpoint flavor with snapshot-aware undo on swap.

    Returns the name of the provider slot that ended up holding the entered
    key after this step — ``provider_name`` for the no-swap (or "Both") case,
    and ``other`` (the opposite flavor) for the swap case. Callers use the
    return value to validate the right slot downstream.
    """
    other = "minimax_anthropic" if provider_name == "minimax" else "minimax"
    entered_cfg = getattr(config.providers, provider_name)
    entered_key = entered_cfg.api_key or ""

    choices = [_FLAVOR_OPENAI, _FLAVOR_ANTHROPIC, _FLAVOR_BOTH]
    pick = _select_with_back("Endpoint flavor:", choices, default=_FLAVOR_BOTH)
    if pick is _BACK_PRESSED or pick is None:
        pick = _FLAVOR_BOTH

    other_base = _MINIMAX_REGION_BASES[(region, other)]

    if pick == _FLAVOR_BOTH:
        # Preserve pre-existing fields on `other` (e.g. extra_headers) by
        # starting from snapshot and overlaying the key + base.
        other_cfg = provider_snapshot[other].model_copy(deep=True)
        other_cfg.api_key = entered_key
        other_cfg.api_base = other_base
        setattr(config.providers, other, other_cfg)
        return provider_name

    entered_is_openai = (provider_name == "minimax")
    pick_is_openai = (pick == _FLAVOR_OPENAI)
    if entered_is_openai == pick_is_openai:
        # Selection matches entered — leave `other` exactly at snapshot.
        setattr(config.providers, other, provider_snapshot[other].model_copy(deep=True))
        return provider_name

    # Swap: user wants the opposite flavor of the one they entered.
    # 1. Move key + base to `other`.
    other_cfg = provider_snapshot[other].model_copy(deep=True)
    other_cfg.api_key = entered_key
    other_cfg.api_base = other_base
    setattr(config.providers, other, other_cfg)
    # 2. Restore `entered` from snapshot (undoes walker writes).
    setattr(
        config.providers, provider_name,
        provider_snapshot[provider_name].model_copy(deep=True),
    )
    # 3. Visible note.
    console.print(
        f"[yellow]Removed the just-entered key from providers.{provider_name}; "
        f"only providers.{other} now holds the MiniMax key.[/yellow]"
    )
    return other


_TIER_STANDARD = "Standard plan (Starter/Plus/Max) — MiniMax-M2.7"
_TIER_HIGHSPEED = "Highspeed plan (Plus-Highspeed/Max-Highspeed/Ultra-Highspeed) — MiniMax-M2.7-highspeed"
_TIER_CUSTOM = "Custom (type a model name)"

_TIER_TO_MODEL = {
    _TIER_STANDARD: "MiniMax-M2.7",
    _TIER_HIGHSPEED: "MiniMax-M2.7-highspeed",
}

# Derived from the schema so a future change to AgentDefaults.model can't
# silently disable the no-clobber guard. Wrapped so a schema rename / nested
# restructure cannot kill the CLI at import time — we'd rather lose the guard
# (no-clobber falls through, user sees a hint instead of a write) than refuse
# to load the wizard.
try:
    _PRISTINE_DEFAULT_MODEL: str = (
        Config.model_fields["agents"].default_factory().defaults.model
    )
except (KeyError, AttributeError, TypeError):
    _PRISTINE_DEFAULT_MODEL = ""


def _minimax_followup_plan_tier_step(config: Config, region: str) -> None:
    """Pick plan tier and (no-clobber) write its model to agents.defaults.model."""
    choices = [_TIER_STANDARD, _TIER_HIGHSPEED, _TIER_CUSTOM]
    pick = _select_with_back("MiniMax plan tier:", choices, default=_TIER_STANDARD)
    if pick is _BACK_PRESSED or pick is None:
        pick = _TIER_STANDARD

    if pick == _TIER_CUSTOM:
        chosen_model = _input_model_with_autocomplete(
            "Default model", config.agents.defaults.model or "", "minimax",
        )
    else:
        chosen_model = _TIER_TO_MODEL[pick]

    if not chosen_model:
        return

    if config.agents.defaults.model == _PRISTINE_DEFAULT_MODEL:
        config.agents.defaults.model = chosen_model
    else:
        console.print(
            f"[dim]Tip: set [cyan]agents.defaults.model[/cyan] to "
            f"[bold]{chosen_model}[/bold] to use it as the default. "
            f"Current default: {config.agents.defaults.model}[/dim]"
        )


def _minimax_followup_validate_step(config: Config, provider_name: str) -> None:
    """Optional: list /models to check the key. 401 clears the key; other
    errors warn and proceed."""
    this_cfg = getattr(config.providers, provider_name)
    api_key = this_cfg.api_key or ""
    api_base = this_cfg.api_base or ""
    if not api_key or not api_base:
        return

    if not _get_questionary().confirm(
        "Verify the key by listing models?", default=True,
    ).ask():
        return

    # Always validate via OpenAI-flavor base — it's stable and key-only.
    if "minimaxi.com" in api_base:
        validate_base = "https://api.minimaxi.com/v1"
    else:
        validate_base = "https://api.minimax.io/v1"
    url = f"{validate_base}/models"

    try:
        resp = httpx.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0,
        )
    except httpx.HTTPError as exc:  # parent of TimeoutException, RequestError, InvalidURL, etc.
        console.print(
            f"[yellow]Couldn't verify the MiniMax key (network: {exc.__class__.__name__}); "
            f"proceeding anyway.[/yellow]"
        )
        return

    if resp.status_code == 401:
        console.print(
            "[red]Invalid MiniMax API key (HTTP 401). Cleared — please re-enter.[/red]"
        )
        this_cfg.api_key = ""
        return

    if resp.status_code != 200:
        console.print(
            f"[yellow]Couldn't verify the MiniMax key (HTTP {resp.status_code}); "
            f"proceeding anyway.[/yellow]"
        )
        return

    body = resp.json()
    data = body.get("data", []) if isinstance(body, dict) else []
    sample_ids = [
        (item.get("id", "?") if isinstance(item, dict) else "?")
        for item in data[:3]
    ]
    console.print(
        f"[green]✓[/green] MiniMax key valid. Sample models: "
        f"{', '.join(sample_ids) if sample_ids else '(none returned)'}"
    )


def _configure_minimax_followup(
    config: Config,
    provider_name: str,
    prev_key: str,
    provider_snapshot: dict,
) -> None:
    """Run the MiniMax-specific follow-up after the field walker.

    Trigger ladder:
      * auto-run when the key arrived or changed this pass
      * offer-run (default N) when an existing MiniMax setup is incomplete/stale
      * no-op otherwise
    """
    this_cfg = getattr(config.providers, provider_name)
    current_key = this_cfg.api_key or ""
    if not current_key:
        return  # walker still didn't get a key — nothing to follow up

    arrived_or_changed = prev_key == "" or prev_key != current_key
    if arrived_or_changed:
        _minimax_followup_run_steps(config, provider_name, provider_snapshot)
        return

    if _minimax_followup_needs_offer(config, provider_name):
        agreed = _get_questionary().confirm(
            "This MiniMax provider is already configured. "
            "Run the region/flavor/model setup again?",
            default=False,
        ).ask()
        if agreed:
            _minimax_followup_run_steps(config, provider_name, provider_snapshot)


def _configure_providers(config: Config) -> None:
    """Configure LLM providers."""

    def get_provider_choices() -> list[str]:
        """Build provider choices with config status indicators.

        OAuth providers (codex, copilot) are shown with an ``(OAuth)`` suffix
        and trigger the login flow when picked — they don't have an api_key
        slot to mark with ``*``.
        """
        from pythinker.providers.registry import PROVIDERS

        spec_by_name = {s.name: s for s in PROVIDERS}
        choices = []
        for name, display in _get_provider_names().items():
            spec = spec_by_name.get(name)
            if spec and spec.is_oauth:
                choices.append(f"{display} (OAuth)")
            else:
                provider = getattr(config.providers, name, None)
                if provider and provider.api_key:
                    choices.append(f"{display} *")
                else:
                    choices.append(display)
        return choices + ["<- Back"]

    while True:
        try:
            console.clear()
            _show_section_header(
                "LLM Providers", "Select a provider to configure API key and endpoint"
            )
            choices = get_provider_choices()
            answer = _select_with_back("Select provider:", choices)

            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":
                break

            # Type guard: answer is now guaranteed to be a string
            assert isinstance(answer, str)
            # Extract provider name from choice (strip " *" / " (OAuth)" suffix)
            provider_name = answer.replace(" *", "").replace(" (OAuth)", "")
            # Find the actual provider key from display names
            for name, display in _get_provider_names().items():
                if display == provider_name:
                    _configure_provider(config, name)
                    break

        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            break


# --- Channel Configuration ---


@lru_cache(maxsize=1)
def _get_channel_info() -> dict[str, tuple[str, type[BaseModel]]]:
    """Get channel info (display name + config class) from channel modules."""
    import importlib

    from pythinker.channels.registry import discover_all

    result: dict[str, tuple[str, type[BaseModel]]] = {}
    for name, channel_cls in discover_all().items():
        try:
            mod = importlib.import_module(f"pythinker.channels.{name}")
            config_name = channel_cls.__name__.replace("Channel", "Config")
            config_cls = getattr(mod, config_name, None)
            if config_cls and isinstance(config_cls, type) and issubclass(config_cls, BaseModel):
                display_name = getattr(channel_cls, "display_name", name.capitalize())
                result[name] = (display_name, config_cls)
        except Exception:
            logger.warning("Failed to load channel module: {}", name)
    return result


def _get_channel_names() -> dict[str, str]:
    """Get channel display names."""
    return {name: info[0] for name, info in _get_channel_info().items()}


def _get_channel_config_class(channel: str) -> type[BaseModel] | None:
    """Get channel config class."""
    entry = _get_channel_info().get(channel)
    return entry[1] if entry else None


def _configure_channel(config: Config, channel_name: str) -> None:
    """Configure a single channel."""
    channel_dict = getattr(config.channels, channel_name, None)
    if channel_dict is None:
        channel_dict = {}
        setattr(config.channels, channel_name, channel_dict)

    display_name = _get_channel_names().get(channel_name, channel_name)
    config_cls = _get_channel_config_class(channel_name)

    if config_cls is None:
        console.print(f"[red]No configuration class found for {display_name}[/red]")
        return

    model = config_cls.model_validate(channel_dict) if channel_dict else config_cls()

    updated_channel = _configure_pydantic_model(
        model,
        display_name,
    )
    if updated_channel is not None:
        new_dict = updated_channel.model_dump(by_alias=True, exclude_none=True)
        setattr(config.channels, channel_name, new_dict)


def _configure_channels(config: Config) -> None:
    """Configure chat channels."""

    def get_channel_choices() -> list[str]:
        """Build channel choices with (configured) suffix on already-enabled ones.

        Mirrors the ' *' suffix pattern that ``_configure_providers`` uses so
        users can tell at a glance which channels already have a working setup.
        """
        choices = []
        for name in _get_channel_names().keys():
            ch = getattr(config.channels, name, None)
            enabled = bool(ch) and (
                ch.get("enabled") if isinstance(ch, dict)
                else getattr(ch, "enabled", False)
            )
            choices.append(f"{name} (configured)" if enabled else name)
        return choices + ["<- Back"]

    while True:
        try:
            console.clear()
            _show_section_header(
                "Chat Channels", "Select a channel to configure connection settings"
            )
            answer = _select_with_back("Select channel:", get_channel_choices())

            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":
                break

            # Type guard: answer is now guaranteed to be a string
            assert isinstance(answer, str)
            # Strip the suffix to recover the channel registry key.
            picked = answer.split(" ", 1)[0]
            _configure_channel(config, picked)
        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            break


# --- General Settings ---

_SETTINGS_SECTIONS: dict[str, tuple[str, str, set[str] | None]] = {
    "Agent Settings": ("Agent Defaults", "Configure default model, temperature, and behavior", None),
    "Channel Common": (
        "Channel Common",
        "Configure cross-channel behavior: progress, tool hints, retries",
        None,
    ),
    "API Server": ("API Server", "Configure OpenAI-compatible API endpoint", None),
    "Gateway": ("Gateway Settings", "Configure server host, port, and heartbeat", None),
    "Tools": ("Tools Settings", "Configure web search, shell exec, and other tools", {"mcp_servers"}),
}

_SETTINGS_GETTER = {
    "Agent Settings": lambda c: c.agents.defaults,
    "Channel Common": lambda c: c.channels,
    "API Server": lambda c: c.api,
    "Gateway": lambda c: c.gateway,
    "Tools": lambda c: c.tools,
}

_SETTINGS_SETTER = {
    "Agent Settings": lambda c, v: setattr(c.agents, "defaults", v),
    "Channel Common": lambda c, v: setattr(c, "channels", v),
    "API Server": lambda c, v: setattr(c, "api", v),
    "Gateway": lambda c, v: setattr(c, "gateway", v),
    "Tools": lambda c, v: setattr(c, "tools", v),
}


def _configure_general_settings(config: Config, section: str) -> None:
    """Configure a general settings section (header + model edit + writeback)."""
    meta = _SETTINGS_SECTIONS.get(section)
    if not meta:
        return
    display_name, subtitle, skip = meta
    model = _SETTINGS_GETTER[section](config)
    updated = _configure_pydantic_model(model, display_name, skip_fields=skip)
    if updated is not None:
        _SETTINGS_SETTER[section](config, updated)


# --- Summary ---


def _summarize_model(obj: BaseModel) -> list[tuple[str, str]]:
    """Recursively summarize a Pydantic model. Returns list of (field, value) tuples."""
    items: list[tuple[str, str]] = []
    for field_name, field_info in type(obj).model_fields.items():
        value = getattr(obj, field_name, None)
        if value is None or value == "" or value == {} or value == []:
            continue
        display = _get_field_display_name(field_name, field_info)
        ftype = _get_field_type_info(field_info)
        if ftype.type_name == "model" and isinstance(value, BaseModel):
            for nested_field, nested_value in _summarize_model(value):
                items.append((f"{display}.{nested_field}", nested_value))
            continue
        formatted = _format_value(value, rich=False, field_name=field_name)
        if formatted != "[not set]":
            items.append((display, formatted))
    return items


def _print_summary_panel(rows: list[tuple[str, str]], title: str) -> None:
    """Build a two-column summary panel and print it."""
    if not rows:
        return
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    for field_name, value in rows:
        table.add_row(field_name, value)
    console.print(Panel(table, title=f"[bold]{title}[/bold]", border_style="blue"))


def _show_summary(config: Config) -> None:
    """Display configuration summary using rich."""
    console.print()

    # Providers
    provider_rows = []
    for name, display in _get_provider_names().items():
        provider = getattr(config.providers, name, None)
        status = (
            "[green]configured[/green]"
            if (provider and provider.api_key)
            else "[dim]not configured[/dim]"
        )
        provider_rows.append((display, status))
    _print_summary_panel(provider_rows, "LLM Providers")

    # Channels
    channel_rows = []
    for name, display in _get_channel_names().items():
        channel = getattr(config.channels, name, None)
        if channel:
            enabled = (
                channel.get("enabled", False)
                if isinstance(channel, dict)
                else getattr(channel, "enabled", False)
            )
            status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        else:
            status = "[dim]not configured[/dim]"
        channel_rows.append((display, status))
    _print_summary_panel(channel_rows, "Chat Channels")

    # Settings sections
    for title, model in [
        ("Agent Settings", config.agents.defaults),
        ("Channel Common", config.channels),
        ("API Server", config.api),
        ("Gateway", config.gateway),
        ("Tools", config.tools),
    ]:
        _print_summary_panel(_summarize_model(model), title)

    _pause()


def _pause() -> None:
    """Pause for user acknowledgement before clearing the screen."""
    _get_questionary().text("Press Enter to continue...", default="").ask()


# --- Web Search Configuration ---

_WEBSEARCH_PROVIDERS: tuple[str, ...] = (
    "duckduckgo", "brave", "tavily", "searxng", "jina", "kagi",
)
_WEBSEARCH_ENV_VARS: dict[str, str] = {
    "brave": "BRAVE_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "jina": "JINA_API_KEY",
    "kagi": "KAGI_API_KEY",
    "searxng": "SEARXNG_BASE_URL",
}


def _websearch_provider_status(provider: str, search_cfg: WebSearchConfig) -> str:
    """Return a bare status hint (no parens). Caller wraps in '(...)' for the label."""
    if provider == "duckduckgo":
        return "free, no key"

    slot = search_cfg.credentials_for(provider)
    is_searxng = provider == "searxng"
    has_slot = bool(slot.base_url) if is_searxng else bool(slot.api_key)
    if has_slot:
        return "✓ configured"

    env_var = _WEBSEARCH_ENV_VARS.get(provider, "")
    if env_var and os.environ.get(env_var, "").strip():
        return "✓ env var"

    return "needs key"


def _run_websearch_live_test(
    search_cfg: WebSearchConfig, proxy: str | None
) -> tuple[bool, str]:
    """Run a one-shot test query. Returns (ok, message).

    Wizard is sync; this bridges into asyncio with a one-shot event loop.
    Returns ``(True, "<first result title>")`` on success or
    ``(False, "<error message>")`` on failure/timeout.
    """
    tool = WebSearchTool(config=search_cfg, proxy=proxy)

    async def _run() -> str:
        return await asyncio.wait_for(
            tool.execute("pythinker test", count=1), timeout=10.0
        )

    try:
        result = asyncio.run(_run())
    except RuntimeError as exc:
        # asyncio.run() refuses if a loop is already running. The wizard
        # is sync, so this branch is unreachable today; let it surface
        # rather than silently swallow a programming error.
        msg = str(exc).lower()
        if "running event loop" in msg or "cannot be called from" in msg:
            raise
        return False, str(exc)
    except asyncio.TimeoutError as exc:
        return False, f"timed out after 10s ({exc})"
    except Exception as exc:
        return False, str(exc)

    if not isinstance(result, str):
        return False, f"unexpected result type: {type(result).__name__}"
    if result.startswith("Error") or result.startswith("No results for:"):
        # Strip leading "Error: " for cleaner display.
        msg = result[len("Error: "):] if result.startswith("Error: ") else result
        return False, msg

    # Pull the first numbered line ("1. <title>") from the formatted output.
    for line in result.splitlines():
        stripped = line.strip()
        if stripped.startswith("1. "):
            return True, stripped[len("1. "):]
    return True, ""  # success but no parseable hit; still treat as ok


def _configure_web_search(config: Config) -> None:
    """Guided web-search setup: pick provider, prompt for key, optional test."""
    search = config.tools.web.search

    # Step 1: Provider picker with status hints.
    choices: list[str] = []
    label_to_provider: dict[str, str] = {}
    for name in _WEBSEARCH_PROVIDERS:
        status = _websearch_provider_status(name, search)
        label = f"{name} ({status})"
        choices.append(label)
        label_to_provider[label] = name

    default_label = next(
        (lbl for lbl, prov in label_to_provider.items() if prov == search.provider),
        choices[0],
    )

    picked = _get_questionary().select(
        "Web search provider:",
        choices=choices,
        default=default_label,
    ).ask()
    if not picked:
        return

    provider = label_to_provider[picked]
    search.provider = provider

    # DuckDuckGo needs no further setup.
    if provider == "duckduckgo":
        return

    is_searxng = provider == "searxng"
    env_var = _WEBSEARCH_ENV_VARS[provider]
    env_value = os.environ.get(env_var, "").strip()

    # Step 2: Env-var detection.
    if env_value:
        use_env = _get_questionary().confirm(
            f"Detected {env_var} in env. Use it?",
            default=True,
        ).ask()
        if use_env is None:
            return  # user pressed Ctrl-C — abort the step entirely
        if use_env:
            # Leave the slot empty so runtime falls through to the env var.
            return

    # Step 3: Prompt for credential.
    if is_searxng:
        prompt = "SearXNG base URL (https://...):"
    else:
        prompt = f"{provider.capitalize()} API key:"

    while True:
        value = _get_questionary().text(prompt, default="").ask()
        if value is None:
            return
        value = value.strip()
        if not value:
            return  # user pressed Enter on empty — abort this step

        if is_searxng:
            # Reuse the URL validator from the web tool.
            from pythinker.agent.tools.web import _validate_url
            ok, err = _validate_url(value)
            if not ok:
                console.print(f"[yellow]! Invalid URL: {err}[/yellow]")
                continue
        break

    slot = search.providers.get(provider) or WebSearchProviderConfig()
    if is_searxng:
        slot.base_url = value
    else:
        slot.api_key = value
    search.providers[provider] = slot

    # Step 4: Optional live test.
    do_test = _get_questionary().confirm(
        "Run a test query to verify?", default=True
    ).ask()
    if not do_test:
        return

    proxy = config.tools.web.proxy
    ok, msg = _run_websearch_live_test(search, proxy=proxy)
    if ok:
        if msg:
            console.print(f"[green]✓ {provider} responded: {msg}[/green]")
        else:
            console.print(f"[green]✓ {provider} responded.[/green]")
    else:
        console.print(f"[red]✗ {provider} test failed: {msg}[/red]")


# --- Main Entry Point ---


def required_headless_flags(cfg: Config | None) -> list[str]:
    """Return the minimum flag set needed for ``pythinker onboard
    --non-interactive`` to complete without prompting, given the current
    state of ``cfg`` (or a fresh ``Config()`` when ``cfg`` is None).

    Flag rules (mirrors pythinker's headless-precondition checker):

    - ``--non-interactive`` and ``--yes-security`` are always required —
      the security disclaimer step exits 1 in headless mode otherwise.
    - ``--auth <provider>`` is required when no provider has a credential
      yet (api_key set, OAuth token on disk, or ``--auth skip``).
    - ``--skip-gateway`` is appended by default — without an explicit
      gateway choice, the wizard prompts. Replace with ``--start-gateway``
      if you want the gateway to start at the end of onboarding.
    """
    from pythinker.providers.registry import PROVIDERS

    flags: list[str] = ["--non-interactive", "--yes-security"]
    cfg = cfg or Config()

    has_provider = False
    for spec in PROVIDERS:
        provider_cfg = getattr(cfg.providers, spec.name, None)
        if provider_cfg is None:
            continue
        api_key = getattr(provider_cfg, "api_key", "") or ""
        if api_key:
            has_provider = True
            break
        if spec.is_oauth:
            try:
                from pythinker.auth import credential_source

                if credential_source(spec, provider_cfg) == "oauth":
                    has_provider = True
                    break
            except Exception:  # noqa: BLE001
                pass
    if not has_provider:
        flags.append("--auth <provider-name-or-skip>")

    flags.append("--skip-gateway")
    return flags


def run_onboard(
    initial_config: Config | None = None,
    *,
    non_interactive: bool = False,
    flow: str | None = None,
    yes_security: bool = False,
    auth: str | None = None,
    auth_method: str | None = None,
    start_gateway: bool | None = None,
    skip_gateway: bool = False,
    reset: str | None = None,
    workspace: str | None = None,
    open_webui: bool = False,
) -> OnboardResult:
    """Run the linear onboarding wizard.

    Replaces the legacy hub-and-spoke menu with the pythinker-style linear
    flow. Steps are registered in `_WIZARD_STEPS` and orchestrated by
    `_run_linear_wizard`.
    """
    if initial_config is not None:
        draft = initial_config.model_copy(deep=True)
    else:
        config_path = get_config_path()
        draft = load_config(config_path) if config_path.exists() else Config()

    ctx = _WizardContext(
        draft=draft,
        flow=flow or "manual",
        non_interactive=non_interactive,
        yes_security=yes_security,
        auth=auth,
        auth_method=auth_method,
        start_gateway=start_gateway,
        skip_gateway=skip_gateway,
        workspace_override=workspace,
        open_webui=open_webui,
    )
    if reset:
        from pythinker.cli.onboard_views.reset import SCOPE_LOOKUP

        ctx.reset_pending = True
        ctx.reset_scope = SCOPE_LOOKUP.get(reset)
    return _run_linear_wizard(ctx)
