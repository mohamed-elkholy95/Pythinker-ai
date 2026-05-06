"""Responses API circuit-breaker helpers."""

from __future__ import annotations

import time

from loguru import logger

_RESPONSES_FAILURE_THRESHOLD = 3
_RESPONSES_PROBE_INTERVAL_S = 300  # 5 minutes


def responses_circuit_key(
    model: str | None,
    default_model: str,
    reasoning_effort: str | None,
) -> str:
    model_name = (model or default_model).lower()
    effort = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
    return f"{model_name}:{effort}"


def circuit_allows_request(
    failures_by_key: dict[str, int],
    tripped_at_by_key: dict[str, float],
    *,
    key: str,
) -> bool:
    failures = failures_by_key.get(key, 0)
    if failures < _RESPONSES_FAILURE_THRESHOLD:
        return True
    tripped = tripped_at_by_key.get(key, 0.0)
    return (time.monotonic() - tripped) >= _RESPONSES_PROBE_INTERVAL_S


def record_responses_failure(
    failures_by_key: dict[str, int],
    tripped_at_by_key: dict[str, float],
    *,
    key: str,
) -> None:
    count = failures_by_key.get(key, 0) + 1
    failures_by_key[key] = count
    if count >= _RESPONSES_FAILURE_THRESHOLD:
        tripped_at_by_key[key] = time.monotonic()
        logger.warning(
            "Responses API circuit open for {} — falling back to Chat Completions",
            key,
        )


def record_responses_success(
    failures_by_key: dict[str, int],
    tripped_at_by_key: dict[str, float],
    *,
    key: str,
) -> None:
    failures_by_key.pop(key, None)
    tripped_at_by_key.pop(key, None)


def should_fallback_from_responses_error(e: Exception) -> bool:
    """Fallback only for likely Responses API compatibility errors."""
    response = getattr(e, "response", None)
    status_code = getattr(e, "status_code", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code not in {400, 404, 422}:
        return False

    body = (
        getattr(e, "body", None)
        or getattr(e, "doc", None)
        or getattr(response, "text", None)
    )
    body_text = str(body).lower() if body is not None else ""
    compatibility_markers = (
        "responses",
        "response api",
        "max_output_tokens",
        "instructions",
        "previous_response",
        "unsupported",
        "not supported",
        "unknown parameter",
        "unrecognized request argument",
    )
    return any(marker in body_text for marker in compatibility_markers)
