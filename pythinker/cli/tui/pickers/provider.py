"""/provider picker.

Pythinker's "active provider" is determined by ``agents.defaults.provider``
plus the prefix of ``agents.defaults.model``. Selection writes both fields
so the model and provider stay coherent.

Each item in the picker is annotated with its readiness state — we look
at the provider's ProviderSpec and the user's config and decide whether
the switch will succeed:

  ✓ ready          — credentials present (api_key or oauth token file)
  ⚠ needs setup    — credentials missing; we surface the signup_url and
                     the exact `pythinker config set` command instead of
                     attempting the switch and hitting an obscure error
                     deep inside the provider factory
  · direct/local   — no auth required (custom or ollama / lm_studio)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from pythinker.cli.models import RECOMMENDED_BY_PROVIDER
from pythinker.cli.tui.pickers.fuzzy import FuzzyPickerScreen

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


def _provider_status(spec, config) -> tuple[str, str]:
    """Return (state, hint) where state ∈ {'ready','needs-setup','no-auth'}.

    Uses the provider factory itself as the source of truth: build a config
    pinned to ``spec.name`` and try to make the provider. Success → ready.
    ValueError → needs-setup with the factory's own message + the spec's
    signup/docs URLs.
    """
    name = getattr(spec, "name", "")
    if getattr(spec, "is_direct", False) or getattr(spec, "is_local", False):
        return "no-auth", ""

    try:
        from pythinker.providers.factory import make_provider
        probe = config.model_copy(deep=True)
        probe.agents.defaults.provider = name
        candidates = RECOMMENDED_BY_PROVIDER.get(name, ())
        if candidates:
            probe.agents.defaults.model = candidates[0]
        make_provider(probe)
        return "ready", ""
    except ValueError as exc:
        signup = getattr(spec, "signup_url", "") or ""
        docs = getattr(spec, "docs_url", "") or ""
        parts: list[str] = [str(exc).strip()]
        if getattr(spec, "is_oauth", False):
            parts.append(f"Run `pythinker auth login --provider {name}`")
        else:
            parts.append(
                f"Set providers.{name}.api_key in ~/.pythinker/config.json "
                f"(or `pythinker config set providers.{name}.api_key <KEY>`)"
            )
        if signup:
            parts.append(f"Sign up: {signup}")
        if docs:
            parts.append(f"Docs: {docs}")
        return "needs-setup", " · ".join(parts)
    except Exception as exc:  # noqa: BLE001 — last-resort defensive
        return "needs-setup", f"unexpected error: {exc!s}"


def _label_for(spec, config) -> str:
    name = getattr(spec, "name", "?")
    display = getattr(spec, "display_name", "") or ""
    state, _ = _provider_status(spec, config)
    badge = {"ready": "✓ ready", "needs-setup": "⚠ setup", "no-auth": "· local"}.get(
        state, ""
    )
    return f"{badge:9s}  {name:24s}  {display}"


def _label_cached(spec, cache: dict[str, tuple[str, str]]) -> str:
    """Build a picker label from a pre-computed status cache."""
    name = getattr(spec, "name", "?")
    display = getattr(spec, "display_name", "") or ""
    state, _ = cache.get(name, ("no-auth", ""))
    badge = {"ready": "✓ ready", "needs-setup": "⚠ setup", "no-auth": "· local"}.get(
        state, ""
    )
    return f"{badge:9s}  {name:24s}  {display}"


def _static_default_model_for(provider_name: str) -> str | None:
    candidates = RECOMMENDED_BY_PROVIDER.get(provider_name, ())
    return candidates[0] if candidates else None


def _openai_models_url(api_base: str) -> str:
    return f"{api_base.rstrip('/')}/models"


async def _local_default_model_for(provider_name: str, config) -> str | None:
    from pythinker.providers.registry import find_by_name

    spec = find_by_name(provider_name)
    if not (spec and spec.is_local):
        return None

    provider_config = getattr(config.providers, provider_name, None)
    api_base = (provider_config.api_base if provider_config else None) or spec.default_api_base
    if not api_base:
        return None

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(_openai_models_url(api_base))
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            return item["id"]
    return None


async def _default_model_for(provider_name: str, config) -> str | None:
    return _static_default_model_for(provider_name) or await _local_default_model_for(
        provider_name,
        config,
    )


async def open_provider_picker(app: "TuiApp") -> None:
    from pythinker.providers.registry import PROVIDERS
    specs = list(PROVIDERS)
    # Pre-compute readiness once so label_fn is O(1) per render instead of
    # calling make_provider() for every item on every keypress.
    _status_cache: dict[str, tuple[str, str]] = {
        getattr(s, "name", ""): _provider_status(s, app.config) for s in specs
    }

    async def _on_select(spec) -> None:
        new_id = getattr(spec, "name", None)
        if not new_id:
            return
        # Pre-flight: refuse the switch if the provider isn't ready, and
        # tell the user exactly what to do.
        state, hint = _provider_status(spec, app.config)
        if state == "needs-setup":
            app.chat_pane.append_notice(
                f"{spec.display_name or new_id} is not configured. {hint}. "
                f"Then re-open `/provider`.",
                kind="warn",
            )
            app.overlay.pop()
            app.application.invalidate()
            return
        try:
            # Use the canonical AgentLoop._apply_provider_snapshot path so the
            # new provider cascades into the runner, subagents, consolidator,
            # and dream — not just self.provider.
            from pythinker.config.loader import get_config_path, save_config
            from pythinker.providers.factory import build_provider_snapshot

            new_config = app.config.model_copy(deep=True)
            new_config.agents.defaults.provider = new_id
            new_model = await _default_model_for(new_id, new_config)
            if new_model:
                new_config.agents.defaults.model = new_model
            snapshot = build_provider_snapshot(new_config)
            app.agent_loop._apply_provider_snapshot(snapshot)  # noqa: SLF001
            app.config = new_config
            if new_model:
                app.state.model = new_model
            app.state.provider = new_id

            persisted = False
            try:
                save_config(new_config, get_config_path())
                persisted = True
            except Exception as save_exc:
                app.chat_pane.append_notice(
                    f"provider swapped in-session, but persisting to disk "
                    f"failed: {save_exc!s}",
                    kind="warn",
                )

            tag = "saved" if persisted else "session-only"
            tail = f"; model → {new_model}" if new_model else ""
            app.chat_pane.append_notice(
                f"provider → {new_id}{tail} ({tag})",
                kind="info",
            )
        except Exception as e:
            app.chat_pane.append_notice(
                f"provider switch failed: {e!s}", kind="error",
            )
        finally:
            app.status_bar.refresh()
            app.overlay.pop()
            app.application.invalidate()

    app.overlay.push(FuzzyPickerScreen(
        items=specs,
        label_fn=lambda s: _label_cached(s, _status_cache),
        on_select=_on_select,
        title="provider",
    ))
