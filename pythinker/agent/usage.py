"""Compute a single (used, limit) pair for the WebUI's context-usage pill.

Uses the same tiktoken-based estimator the autocompact path falls back to
so the displayed number is consistent across surfaces. ``used`` is clamped
at ``limit`` so the pill never renders >100% (autocompact will catch up
on the next turn).
"""
from __future__ import annotations

from typing import TypedDict

from loguru import logger

from pythinker.config.schema import AgentDefaults
from pythinker.session.manager import Session
from pythinker.utils.helpers import estimate_prompt_tokens


class SessionUsage(TypedDict):
    used: int
    limit: int


def estimate_session_usage(session: Session, defaults: AgentDefaults) -> SessionUsage:
    """Return the WebUI-friendly token usage snapshot for *session*.

    Skips the chain helper (which needs a live provider instance) and goes
    straight to the tiktoken counter — sufficient precision for a UI pill.
    A warning is logged when the counter returns 0 for a non-empty session
    so operators can spot a broken tiktoken install (the UI would otherwise
    show a misleading 0%).
    """
    if not session.messages:
        return {"used": 0, "limit": defaults.context_window_tokens}
    used = estimate_prompt_tokens(session.messages, None)
    if used == 0:
        logger.warning(
            "estimate_prompt_tokens returned 0 for a non-empty session; "
            "tiktoken may be unavailable"
        )
    return {
        "used": min(used, defaults.context_window_tokens),
        "limit": defaults.context_window_tokens,
    }
