"""Token-counting utilities (tiktoken-based with provider fallback)."""
from __future__ import annotations

import json
from typing import Any


def _get_encoding(name: str) -> Any:
    import tiktoken
    try:
        return tiktoken.get_encoding(name)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    encoding: str = "cl100k_base",
) -> int:
    """Estimate prompt tokens with tiktoken using ``encoding``."""
    try:
        enc = _get_encoding(encoding)
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = part.get("text", "")
                        if txt:
                            parts.append(txt)
            if msg.get("tool_calls"):
                parts.append(json.dumps(msg["tool_calls"], ensure_ascii=False))
            rc = msg.get("reasoning_content")
            if isinstance(rc, str) and rc:
                parts.append(rc)
            for key in ("name", "tool_call_id"):
                value = msg.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
        if tools:
            parts.append(json.dumps(tools, ensure_ascii=False))
        return len(enc.encode("\n".join(parts))) + len(messages) * 4
    except Exception:
        return 0


def estimate_message_tokens(message: dict[str, Any], *, encoding: str = "cl100k_base") -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))
    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        parts.append(rc)
    payload = "\n".join(parts)
    if not payload:
        return 4
    try:
        return max(4, len(_get_encoding(encoding).encode(payload)) + 4)
    except Exception:
        return max(4, len(payload) // 4 + 4)


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    encoding: str = "cl100k_base",
) -> tuple[int, str]:
    """Estimate prompt tokens synchronously via provider-local then tiktoken counters."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        try:
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
        except Exception:
            pass
    estimated = estimate_prompt_tokens(messages, tools, encoding=encoding)
    return (int(estimated), f"tiktoken:{encoding}") if estimated > 0 else (0, "none")


async def async_estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    encoding: str = "cl100k_base",
) -> tuple[int, str]:
    """Estimate prompt tokens, preferring async provider counters when present."""
    async_counter = getattr(provider, "async_estimate_prompt_tokens", None)
    if callable(async_counter):
        try:
            tokens, source = await async_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
        except Exception:
            pass
    return estimate_prompt_tokens_chain(provider, model, messages, tools, encoding=encoding)
