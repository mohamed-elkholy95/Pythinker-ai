"""Search provider picker step."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext
from pythinker.config.schema import Config


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
    from pythinker.cli import onboard as _onboard
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
        ("duckduckgo", "DuckDuckGo Search", "free, no key"),
        ("tavily", "Tavily Search", "TAVILY_API_KEY · structured results"),
        ("brave", "Brave Search", "BRAVE_API_KEY"),
        ("perplexity", "Perplexity Search", "PERPLEXITY_API_KEY"),
    ):
        display, h = _label(opt_id, base, hint)
        options.append((opt_id, display, h))
    if current_provider in configured_providers:
        options.insert(0, ("__keep__", f"Keep current ({current_provider})", "no changes"))
    options.append(("skip", "Skip for now", ""))

    option_ids = {opt_id for opt_id, _, _ in options}
    default = "__keep__" if current_provider in configured_providers else (
        current_provider if current_provider in option_ids else "tavily"
    )
    chosen = clack.select(
        "Search provider", options=options, default=default, searchable=True
    )

    if chosen in ("skip", "__keep__"):
        return StepResult(status="continue")
    if chosen == "duckduckgo":
        _activate_search_provider(ctx.draft, chosen)
        clack.print_status("Search provider: duckduckgo (free, no key)")
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
    _onboard._emit_docs_link("search")
    return StepResult(status="continue")
