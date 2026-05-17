"""Best-effort token cost estimation from model metadata pricing rows."""

from __future__ import annotations

from typing import Any, TypedDict

from pythinker.providers.model_metadata import get_model_metadata


class EstimatedCost(TypedDict):
    cost: float
    currency: str
    input_cost: float
    cached_input_cost: float
    output_cost: float


def _usage_int(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0


def estimate_usage_cost(model: str, usage: dict[str, Any]) -> EstimatedCost | None:
    """Return estimated USD-like cost for a single model turn, when known.

    Pricing is intentionally metadata-driven and best-effort: unknown models or
    rows without pricing return ``None`` so the UI can keep showing "Not tracked"
    instead of inventing costs.
    """
    meta = get_model_metadata(model)
    if meta is None or meta.input_cost_per_million is None or meta.output_cost_per_million is None:
        return None

    prompt = _usage_int(usage, "prompt_tokens")
    completion = _usage_int(usage, "completion_tokens")
    cached = min(_usage_int(usage, "cached_tokens"), prompt)
    uncached = max(prompt - cached, 0)

    input_rate = meta.input_cost_per_million
    cached_rate = meta.cached_input_cost_per_million
    output_rate = meta.output_cost_per_million
    if cached_rate is None:
        cached_rate = input_rate

    if (
        meta.long_context_threshold_tokens is not None
        and prompt > meta.long_context_threshold_tokens
    ):
        input_rate *= meta.long_context_input_multiplier or 1.0
        cached_rate *= meta.long_context_input_multiplier or 1.0
        output_rate *= meta.long_context_output_multiplier or 1.0

    input_cost = uncached * input_rate / 1_000_000
    cached_cost = cached * cached_rate / 1_000_000
    output_cost = completion * output_rate / 1_000_000
    total = input_cost + cached_cost + output_cost
    return {
        "cost": round(total, 10),
        "currency": meta.currency or "USD",
        "input_cost": round(input_cost, 10),
        "cached_input_cost": round(cached_cost, 10),
        "output_cost": round(output_cost, 10),
    }
