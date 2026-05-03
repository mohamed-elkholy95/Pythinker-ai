"""Live model discovery for local OpenAI-compatible providers.

Static catalogs (``pythinker/cli/models.py:RECOMMENDED_BY_PROVIDER``) are
fine for paid providers where the model list is stable, but for local
servers (LM Studio, Ollama, vLLM, OVMS, custom) the user changes which
model is loaded at any time — a stale catalog produces confusing
"Failed to load model" errors when the configured id no longer exists
on the server.

This module asks each local server what it currently offers.

Best-practice notes (verified via context7 docs, 2026-04-30):

* **LM Studio** exposes both ``GET /v1/models`` (OpenAI-compat) and the
  richer ``GET /api/v1/models`` (native) which includes a
  ``loaded_instances`` array per model. We prefer the native endpoint
  because it lets us mark which models are currently in memory vs. only
  downloaded; we fall back to ``/v1/models`` if the native call fails.

* **Ollama** exposes ``GET /api/tags`` (downloaded models) and
  ``GET /api/ps`` (currently *running* models, with VRAM and TTL info).
  We hit both so the picker can show ``● loaded`` vs ``○ downloaded``.
  The OpenAI-compat ``GET /v1/models`` works too but is strictly less
  informative.

* **Generic OpenAI-compat** (vLLM, OVMS, custom): ``GET /v1/models`` is
  the only universally-supported endpoint, so that's the fallback path.

* All calls are short-timeout (default 2.0 s) and short-TTL cached
  (default 15 s) so opening the ``/model`` picker doesn't hammer the
  local server. Errors are swallowed and logged; callers receive an
  empty list and surface a friendly "is the server running?" message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

import httpx
from loguru import logger


@dataclass(frozen=True)
class LocalModel:
    """A single model discovered on a local OpenAI-compatible server.

    Fields are deliberately minimal so this can be backed by either the
    OpenAI-compat ``/v1/models`` shape (id only) or the richer native
    LM Studio / Ollama shapes.
    """

    model_id: str
    """The id you'd send in a chat-completions request (e.g. ``llama3.2``)."""

    loaded: bool = False
    """``True`` when the server reports the model is currently in memory.
    Native LM Studio + Ollama can answer this; OpenAI-compat ``/v1/models``
    cannot, so this stays ``False`` on the fallback path."""

    size_bytes: int | None = None
    """Bytes on disk, when the server reports it."""

    parameter_size: str | None = None
    """Human label like ``7B`` / ``13B``, when the server reports it."""

    quantization: str | None = None
    """Quantization label like ``Q4_K_M``, when the server reports it."""

    detail: str | None = None
    """Free-form extra info (e.g. ``loaded for 30s``) — purely cosmetic."""


@dataclass
class _CacheEntry:
    fetched_at: float
    models: list[LocalModel] = field(default_factory=list)


_CACHE: dict[str, _CacheEntry] = {}


def _cache_get(key: str, ttl_s: float) -> list[LocalModel] | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    if time.monotonic() - entry.fetched_at > ttl_s:
        return None
    return entry.models


def _cache_set(key: str, models: list[LocalModel]) -> None:
    _CACHE[key] = _CacheEntry(fetched_at=time.monotonic(), models=list(models))


def clear_cache() -> None:
    """Drop the in-process cache. Tests + ``/model`` "refresh" call this."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Per-provider fetchers
# ---------------------------------------------------------------------------


def _strip_v1(api_base: str) -> str:
    """``http://host:1234/v1`` → ``http://host:1234`` so we can hit
    LM Studio's native ``/api/v1/models`` and Ollama's ``/api/ps``."""
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    if base.endswith("/v3"):  # OVMS
        return base[:-3]
    return base


async def _fetch_lm_studio(client: httpx.AsyncClient, api_base: str) -> list[LocalModel]:
    """Prefer LM Studio's native ``/api/v1/models``; fall back to ``/v1/models``.

    Native shape (per LM Studio Developer Docs, 2026-04-30):

        {"models": [
          {"key": "...", "display_name": "...", "loaded_instances": [...],
           "size_bytes": ..., "params_string": "7B",
           "quantization": {"name": "q4_0", ...}}
        ]}
    """
    host = _strip_v1(api_base)
    try:
        r = await client.get(f"{host}/api/v1/models")
        r.raise_for_status()
        payload = r.json()
        models: list[LocalModel] = []
        for m in payload.get("models", []) or []:
            key = m.get("key") or m.get("display_name")
            if not key:
                continue
            quant = m.get("quantization") or {}
            models.append(LocalModel(
                model_id=key,
                loaded=bool(m.get("loaded_instances")),
                size_bytes=m.get("size_bytes"),
                parameter_size=m.get("params_string"),
                quantization=quant.get("name") if isinstance(quant, dict) else None,
                detail=m.get("display_name"),
            ))
        if models:
            return models
    except httpx.HTTPError as exc:
        logger.debug("LM Studio /api/v1/models failed, falling back: {}", exc)
    return await _fetch_openai_compat(client, api_base)


async def _fetch_ollama(client: httpx.AsyncClient, api_base: str) -> list[LocalModel]:
    """Merge ``/api/tags`` (downloaded) with ``/api/ps`` (running) so the
    picker can mark which models are hot vs. idle."""
    host = _strip_v1(api_base)
    downloaded: dict[str, LocalModel] = {}
    try:
        r = await client.get(f"{host}/api/tags")
        r.raise_for_status()
        for m in r.json().get("models", []) or []:
            mid = m.get("model") or m.get("name")
            if not mid:
                continue
            details = m.get("details") or {}
            downloaded[mid] = LocalModel(
                model_id=mid,
                loaded=False,
                size_bytes=m.get("size"),
                parameter_size=details.get("parameter_size") if isinstance(details, dict) else None,
                quantization=details.get("quantization_level") if isinstance(details, dict) else None,
            )
    except httpx.HTTPError as exc:
        logger.debug("Ollama /api/tags failed: {}", exc)

    try:
        r = await client.get(f"{host}/api/ps")
        r.raise_for_status()
        for m in r.json().get("models", []) or []:
            mid = m.get("model") or m.get("name")
            if not mid:
                continue
            existing = downloaded.get(mid)
            details = m.get("details") or {}
            downloaded[mid] = LocalModel(
                model_id=mid,
                loaded=True,
                size_bytes=(existing.size_bytes if existing else m.get("size")),
                parameter_size=(
                    details.get("parameter_size")
                    if isinstance(details, dict) else
                    (existing.parameter_size if existing else None)
                ),
                quantization=(
                    details.get("quantization_level")
                    if isinstance(details, dict) else
                    (existing.quantization if existing else None)
                ),
                detail="running",
            )
    except httpx.HTTPError as exc:
        logger.debug("Ollama /api/ps failed: {}", exc)

    if downloaded:
        return list(downloaded.values())
    # Both native endpoints failed (or empty) — last-ditch OpenAI fallback.
    return await _fetch_openai_compat(client, api_base)


async def _fetch_openai_compat(
    client: httpx.AsyncClient, api_base: str
) -> list[LocalModel]:
    """Universal OpenAI-compatible fallback: ``GET /v1/models`` → ids only.

    The endpoint is mandatory in the OpenAI compat surface so anything
    that *claims* OpenAI compatibility (vLLM, OVMS, llama-server, etc.)
    must serve it.
    """
    base = api_base.rstrip("/")
    # Some servers serve /v1/models alongside the api_base directly; others
    # mount /v1 inside the api_base. Try the api_base form first, then
    # the host-root form so we work for both layouts.
    candidates = [f"{base}/models"]
    if base.endswith("/v1"):
        candidates.append(f"{base}/models")  # already correct, dedup below
    else:
        candidates.append(f"{base.rstrip('/')}/v1/models")
    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                continue
            models = [
                LocalModel(model_id=item["id"])
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            if models:
                return models
        except httpx.HTTPError as exc:
            logger.debug("GET {} failed: {}", url, exc)
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_LOCAL_FETCHERS = {
    "lm_studio": _fetch_lm_studio,
    "ollama": _fetch_ollama,
}


async def list_local_models(
    *,
    provider_id: str,
    api_base: str | None,
    timeout_s: float = 2.0,
    cache_ttl_s: float = 15.0,
) -> list[LocalModel]:
    """Return the models a local OpenAI-compatible server is currently serving.

    Returns an empty list (and logs at DEBUG) when the server is unreachable,
    the response is malformed, or the provider isn't recognised. Callers
    are expected to surface a friendly "is the server running?" message
    when this returns empty for a configured local provider.
    """
    if not api_base:
        return []
    cache_key = f"{provider_id}|{api_base}"
    cached = _cache_get(cache_key, cache_ttl_s)
    if cached is not None:
        return cached

    fetcher = _LOCAL_FETCHERS.get(provider_id, _fetch_openai_compat)
    timeout = httpx.Timeout(timeout_s, connect=min(1.0, timeout_s))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            models = await fetcher(client, api_base)
    except httpx.HTTPError as exc:
        logger.debug("list_local_models({}) network failure: {}", provider_id, exc)
        models = []
    except Exception:  # noqa: BLE001 — last-resort guard, never fail the caller
        logger.exception("list_local_models({}) unexpected failure", provider_id)
        models = []

    _cache_set(cache_key, models)
    return models


def coerce_iter(models: Iterable[LocalModel] | None) -> list[LocalModel]:
    """Normalize ``None`` / generators to a concrete list (Typer-safe)."""
    if models is None:
        return []
    return list(models)


__all__ = [
    "LocalModel",
    "clear_cache",
    "coerce_iter",
    "list_local_models",
]
