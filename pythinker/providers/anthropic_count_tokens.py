"""Anthropic messages/count_tokens client with TTL cache and graceful fallback."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True, slots=True)
class CountTokensResult:
    input_tokens: int
    original_input_tokens: int | None
    source: str


_Transport = Callable[..., Awaitable[dict[str, Any]]]
_Sleep = Callable[[float], Awaitable[None]]
_System = str | list[dict[str, Any]] | None


class AnthropicCountTokensClient:
    """Cached, retry-aware wrapper around Anthropic's count_tokens endpoint."""

    def __init__(
        self,
        *,
        transport: _Transport,
        cache_ttl_s: float = 60.0,
        max_retries: int = 2,
        sleep: _Sleep | None = None,
    ) -> None:
        self._transport = transport
        self._ttl = cache_ttl_s
        self._max_retries = max(1, max_retries)
        self._sleep = sleep or _async_sleep
        self._cache: dict[str, tuple[float, CountTokensResult]] = {}

    def _key(
        self,
        *,
        messages: list[dict[str, Any]],
        system: _System,
        tools: list[dict[str, Any]] | None,
        model: str,
    ) -> str:
        payload = json.dumps(
            {"m": messages, "s": system, "t": tools, "model": model},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return hashlib.blake2b(payload, digest_size=16).hexdigest()

    async def count(
        self,
        *,
        messages: list[dict[str, Any]],
        system: _System,
        tools: list[dict[str, Any]] | None,
        model: str,
    ) -> CountTokensResult | None:
        key = self._key(messages=messages, system=system, tools=tools, model=model)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) < self._ttl:
            return cached[1]

        body: dict[str, Any] = {"messages": messages, "model": model}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._transport(body=body)
                tokens = int(resp.get("input_tokens") or 0)
                if tokens <= 0:
                    return None
                context_management = resp.get("context_management") or {}
                original = context_management.get("original_input_tokens")
                result = CountTokensResult(
                    input_tokens=tokens,
                    original_input_tokens=int(original) if original is not None else None,
                    source="anthropic_count_tokens",
                )
                self._cache[key] = (now, result)
                return result
            except Exception as e:
                retry_after = _retry_after_seconds(e)
                retryable = retry_after is not None or _is_transient_count_error(e)
                if retryable and attempt < self._max_retries:
                    await self._sleep(retry_after or min(8.0, 2.0 ** (attempt - 1)))
                    continue
                logger.warning("anthropic count_tokens failed: {}", e)
                return None
        return None


def _is_transient_count_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    markers = ("429", "rate_limit", "overloaded", "500", "502", "503", "504", "timeout")
    return any(marker in msg for marker in markers)


def _retry_after_seconds(exc: Exception) -> float | None:
    msg = str(exc).lower()
    if "retry-after=" not in msg:
        return None
    try:
        return min(60.0, max(0.0, float(msg.rsplit("retry-after=", 1)[1].split()[0])))
    except ValueError:
        return None


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
