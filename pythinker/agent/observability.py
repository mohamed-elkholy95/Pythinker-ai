"""Structured turn-level observability for context-window operations."""
from __future__ import annotations

import hashlib
from typing import Any

from loguru import logger

_SESSION_KEY_HASH_LEN = 12


def _redact_session_key(session_key: str) -> str:
    """Hash the chat/user identifier while preserving the channel prefix."""
    if ":" not in session_key:
        digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
        return digest[:_SESSION_KEY_HASH_LEN]
    channel, chat_id = session_key.split(":", 1)
    digest = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()
    return f"{channel}:{digest[:_SESSION_KEY_HASH_LEN]}"


def emit_context_turn_event(
    *,
    session_key: str,
    model: str,
    window: int,
    floor: int,
    prompt_est: int,
    prompt_actual: int | None,
    zone: str,
    action: str,
    snip: bool,
    microcompact: int,
    encoding: str,
    metadata_source: str,
    **extra: Any,
) -> None:
    """Log one structured ``context_turn`` event with PII-safe session key."""
    payload = {
        "event": "context_turn",
        "session": _redact_session_key(session_key),
        "model": model,
        "window": window,
        "floor": floor,
        "prompt_est": prompt_est,
        "prompt_actual": prompt_actual,
        "drift": (prompt_actual - prompt_est) if prompt_actual is not None else None,
        "zone": zone,
        "action": action,
        "snip": snip,
        "microcompact": microcompact,
        "encoding": encoding,
        "metadata_source": metadata_source,
        **extra,
    }
    logger.info("context_turn {}", payload)
