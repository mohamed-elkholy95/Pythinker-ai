"""Compute a single (used, limit) pair for the WebUI's context-usage pill.

Uses the same tiktoken-based estimator the autocompact path falls back to
so the displayed number is consistent across surfaces. ``used`` is clamped
at ``limit`` so the pill never renders >100% (autocompact will catch up
on the next turn).
"""
from __future__ import annotations

from typing import Literal, TypedDict

from loguru import logger

from pythinker.config.schema import AgentDefaults
from pythinker.providers.model_profiles import get_profile
from pythinker.session.manager import Session
from pythinker.utils.helpers import estimate_prompt_tokens


class SessionUsage(TypedDict, total=False):
    used: int
    limit: int
    floor: int
    floor_status: Literal["ok", "unavailable", "skipped"]


def _usage_limit(defaults: AgentDefaults) -> int:
    profile = get_profile(defaults.model)
    cap = profile.input if profile else None
    configured = defaults.context_window_tokens
    if configured is None:
        return cap or 65_536
    if cap is not None and configured > cap:
        return cap
    return configured


def estimate_session_usage(
    session: Session,
    defaults: AgentDefaults,
    *,
    encoding: str = "cl100k_base",
    floor_tokens: int = 0,
    floor_status: Literal["ok", "unavailable", "skipped"] = "skipped",
) -> SessionUsage:
    """Return the WebUI-friendly token usage snapshot for *session*.

    Skips the chain helper (which needs a live provider instance) and goes
    straight to the tiktoken counter — sufficient precision for a UI pill.
    A warning is logged when the counter returns 0 for a non-empty session
    so operators can spot a broken tiktoken install (the UI would otherwise
    show a misleading 0%).
    """
    limit = _usage_limit(defaults)
    if not session.messages:
        return {
            "used": 0,
            "limit": limit,
            "floor": floor_tokens,
            "floor_status": floor_status,
        }
    used = estimate_prompt_tokens(session.messages, None, encoding=encoding)
    if used == 0:
        logger.warning(
            "estimate_prompt_tokens returned 0 for a non-empty session; "
            "tiktoken may be unavailable"
        )
    return {
        "used": min(used, limit),
        "limit": limit,
        "floor": floor_tokens,
        "floor_status": floor_status,
    }
