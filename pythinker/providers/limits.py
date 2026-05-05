"""Provider input/output token-limit helpers.

Pure-functional helpers that depend only on a ``LLMProvider`` instance and a
model id. Lives outside ``pythinker/agent/loop.py`` so the loop can stay
focused on lifecycle, dispatch, and checkpointing.
"""

from __future__ import annotations

from loguru import logger

from pythinker.providers.base import LLMProvider


def clamp_context_window(provider: LLMProvider, model: str, configured: int) -> int:
    """Clamp ``configured`` to the provider's published input cap.

    Some plans publish hard limits the server enforces (e.g. ChatGPT/Codex
    OAuth caps gpt-5.5 input at 272k tokens). Without this, configured
    windows exceeding the cap drive silent server-side overflow after
    compaction has already trusted the larger budget.
    """
    limits = provider.get_model_limits(model)
    if not isinstance(limits, dict):
        return configured
    input_cap = limits.get("input")
    if not isinstance(input_cap, int) or input_cap <= 0:
        return configured
    if configured > input_cap:
        logger.info(
            "Clamping context_window_tokens {} → {} for model {} (provider cap)",
            configured, input_cap, model,
        )
        return input_cap
    return configured


# Compatibility alias so ``from pythinker.providers.limits import _clamp_context_window``
# also works for any caller that retained the leading-underscore name during the move.
_clamp_context_window = clamp_context_window
