"""/model picker.

Data source: union of
  - the current active model
  - config.agents.defaults.alternate_models
  - **for local providers** (LM Studio / Ollama / vLLM / OVMS / custom):
    a live ``/v1/models`` (or richer native endpoint) probe so the
    picker reflects what the server is actually serving *right now*
    — not whatever the static catalog claims is recommended.
  - pythinker.cli.models.RECOMMENDED_BY_PROVIDER (the real per-provider
    catalog used by the onboarding wizard) for paid providers and as
    a fallback if the live probe fails.

Provider id is resolved via config.get_provider_name(model) — the
canonical pythinker mapping that handles every registered provider
(including `openai_codex`, `github_copilot`, `minimax_anthropic`, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythinker.cli.models import RECOMMENDED_BY_PROVIDER
from pythinker.cli.tui.pickers.fuzzy import FuzzyPickerScreen
from pythinker.providers.local_models import LocalModel, list_local_models
from pythinker.providers.registry import find_by_name

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


def _api_base_for(provider_id: str | None, config) -> str | None:
    """Return the configured api_base for ``provider_id``, falling back to
    the registry's ``default_api_base`` when the user hasn't overridden it."""
    if not provider_id:
        return None
    spec = find_by_name(provider_id)
    p = getattr(config.providers, provider_id, None)
    base = getattr(p, "api_base", None) if p else None
    return base or (spec.default_api_base if spec else None)


async def _local_model_items(provider_id: str, config) -> list[dict]:
    """Fetch live models from a local server. Returns items pre-tagged
    'loaded' / 'downloaded' / 'available' so the label can mark which
    are hot in memory vs. only on disk."""
    api_base = _api_base_for(provider_id, config)
    if not api_base:
        return []
    raw: list[LocalModel] = await list_local_models(
        provider_id=provider_id, api_base=api_base
    )
    items: list[dict] = []
    for m in raw:
        items.append({
            "model_id": m.model_id,
            "source": "loaded" if m.loaded else "downloaded",
            "provider": provider_id,
            "_local": m,
        })
    return items


def build_model_items(*, loop, config) -> list[dict]:
    """Sync builder used by tests + the synchronous fast-path. Local
    discovery happens in the async ``open_model_picker`` so the picker
    can ``await`` the network probe without freezing the UI; this sync
    builder still works (returns just the static catalog) for tests."""
    current = getattr(loop, "model", None)
    provider_id = _resolve_provider_id(loop, config, current)

    seen: set[str] = set()
    items: list[dict] = []

    def _add(model_id: str, source: str, **extra: object) -> None:
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        items.append({
            "model_id": model_id, "source": source, "provider": provider_id, **extra,
        })

    if current:
        _add(current, "current")

    for m in getattr(config.agents.defaults, "alternate_models", []) or []:
        _add(m, "alternate")

    if provider_id and provider_id in RECOMMENDED_BY_PROVIDER:
        for m in RECOMMENDED_BY_PROVIDER[provider_id]:
            _add(m, "available")
    elif provider_id:
        # Provider is known but has no static catalog (typical for local
        # servers like lm_studio / ollama). Don't dump every other
        # provider's models — surface a placeholder so the user knows
        # this picker has nothing pre-baked for their backend.
        pass
    else:
        for prov, models in sorted(RECOMMENDED_BY_PROVIDER.items()):
            for m in models:
                _add(m, prov)

    return items


def _merge_local_first(
    static_items: list[dict], local_items: list[dict]
) -> list[dict]:
    """Local probe wins over the static catalog when both list the same
    model id. The static catalog is kept as a fallback for typing
    aliases the user may have learned from earlier sessions."""
    seen: set[str] = set()
    out: list[dict] = []
    # Promote loaded models to the top so the picker default is something
    # the server can actually serve right now.
    local_items = sorted(
        local_items, key=lambda it: (0 if it.get("source") == "loaded" else 1)
    )
    for it in local_items + static_items:
        mid = it["model_id"]
        if mid in seen:
            continue
        seen.add(mid)
        out.append(it)
    return out


def _resolve_provider_id(loop, config, current_model: str | None) -> str | None:
    # 1) cached on the loop (some providers set this).
    pid = getattr(loop, "provider_id", None)
    if pid:
        return pid
    # 2) pythinker's canonical resolver (handles all registered providers).
    if current_model:
        try:
            name = config.get_provider_name(current_model)
            if name:
                return name
        except Exception:
            pass
    # 3) fallback prefix sniff.
    m = current_model or ""
    if m.startswith(("gpt-", "o3", "o4")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    return None


def _trunc(s: str, n: int) -> str:
    """Hard-truncate ``s`` to ``n`` chars with a trailing ellipsis if clipped."""
    return s if len(s) <= n else s[:n - 1] + "…"


def _label(item: dict) -> str:
    badge = {
        "current": "●",
        "loaded": "●",
        "alternate": "○",
        "downloaded": "○",
        "fallback": "·",
    }.get(item["source"], " ")
    suffix = item["source"]
    local: LocalModel | None = item.get("_local")  # type: ignore[assignment]
    if isinstance(local, LocalModel):
        bits = []
        if local.parameter_size:
            bits.append(local.parameter_size)
        if local.quantization:
            bits.append(local.quantization)
        if bits:
            suffix = f"{suffix} · {' '.join(bits)}"
    return f"{badge}  {_trunc(item['model_id'], 30):30s}  ({suffix})"


async def open_model_picker(app: "TuiApp") -> None:
    static_items = build_model_items(loop=app.agent_loop, config=app.config)
    provider_id = _resolve_provider_id(
        app.agent_loop, app.config, getattr(app.agent_loop, "model", None)
    )
    spec = find_by_name(provider_id) if provider_id else None
    items = static_items
    if spec is not None and getattr(spec, "is_local", False):
        local_items = await _local_model_items(provider_id, app.config)
        if local_items:
            items = _merge_local_first(static_items, local_items)
        elif not static_items:
            # Local provider, server unreachable, no static catalog —
            # tell the user instead of showing an empty picker.
            api_base = _api_base_for(provider_id, app.config) or "<no api_base>"
            app.chat_pane.append_notice(
                f"no models reported by {provider_id} at {api_base}. "
                f"Is the server running and a model loaded?",
                kind="warn",
            )

    async def _on_select(item: dict) -> None:
        try:
            from pythinker.config.loader import get_config_path, save_config
            from pythinker.providers.factory import build_provider_snapshot

            new_config = app.config.model_copy(deep=True)
            new_config.agents.defaults.model = item["model_id"]
            # Cascade through the runner, subagents, consolidator, and dream
            # so the next turn actually targets the new model.
            snapshot = build_provider_snapshot(new_config)
            app.agent_loop._apply_provider_snapshot(snapshot)  # noqa: SLF001
            app.config = new_config
            app.state.model = item["model_id"]

            persisted = False
            try:
                save_config(new_config, get_config_path())
                persisted = True
            except Exception as save_exc:
                app.chat_pane.append_notice(
                    f"model swapped in-session, but persisting to disk failed: "
                    f"{save_exc!s}",
                    kind="warn",
                )

            tag = "saved" if persisted else "session-only"
            app.chat_pane.append_notice(
                f"model → {item['model_id']} ({tag})",
                kind="info",
            )
        except Exception as e:
            app.chat_pane.append_notice(f"model switch failed: {e!s}", kind="error")
        finally:
            app.status_bar.refresh()
            app.overlay.pop()
            app.application.invalidate()

    app.overlay.push(FuzzyPickerScreen(
        items=items, label_fn=_label, on_select=_on_select, title="model",
    ))
