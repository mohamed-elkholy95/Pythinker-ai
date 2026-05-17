"""Provider input-window derivation helpers."""

from __future__ import annotations

from loguru import logger

from pythinker.providers.base import LLMProvider
from pythinker.providers.model_profiles import get_profile

_LEGACY_DEFAULT = 65_536


def derive_window(provider: LLMProvider, model: str, configured: int | None) -> int:
    """Pick the largest safe input window for ``model``."""
    profile = get_profile(model)
    provider_cap = None
    try:
        limits = provider.get_model_limits(model)
    except Exception as exc:
        logger.debug("provider.get_model_limits({}) raised: {}", model, exc)
        limits = None
    if isinstance(limits, dict):
        cap = limits.get("input")
        if isinstance(cap, int) and cap > 0:
            provider_cap = cap
    cap = provider_cap if provider_cap is not None else (profile.input if profile else None)
    if configured is None:
        return cap or _LEGACY_DEFAULT
    if cap is not None and configured > cap:
        logger.info("Clamping context_window_tokens {} → {} for model {} (cap)", configured, cap, model)
        return cap
    return configured


def clamp_context_window(provider: LLMProvider, model: str, configured: int | None) -> int:
    """Backwards-compatible one-way clamp wrapper."""
    return derive_window(provider, model, configured)


_clamp_context_window = clamp_context_window
