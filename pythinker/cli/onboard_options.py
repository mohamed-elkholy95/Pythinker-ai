"""Provider-picker option-builder helpers used by the onboarding wizard.

Split out of ``pythinker/cli/onboard.py``. ``onboard.py`` re-exports each
public name here so existing test imports off
``pythinker.cli.onboard`` keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythinker.cli.models import RECOMMENDED_BY_PROVIDER

if TYPE_CHECKING:
    from pythinker.providers.registry import ProviderSpec


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
