"""OpenAI Responses API request helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pythinker.providers.base import LLMResponse
from pythinker.providers.openai_responses.converters import convert_messages, convert_tools
from pythinker.providers.openai_responses.parsing import consume_sdk_stream, parse_response_output


def is_direct_openai_base(api_base: str | None) -> bool:
    """Return True for direct OpenAI endpoints, not generic compatible gateways."""
    if not api_base:
        return True
    normalized = api_base.strip().lower().rstrip("/")
    return "api.openai.com" in normalized and "openrouter" not in normalized


def build_responses_body(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model_name: str,
    max_tokens: int,
    temperature: float,
    reasoning_effort: str | None,
    tool_choice: str | dict[str, Any] | None,
    supports_temperature: bool,
) -> dict[str, Any]:
    """Build a Responses API body from sanitized Chat-Completions-style args."""
    instructions, input_items = convert_messages(messages)
    body: dict[str, Any] = {
        "model": model_name,
        "instructions": instructions or None,
        "input": input_items,
        "max_output_tokens": max(1, max_tokens),
        "store": False,
        "stream": False,
    }

    if supports_temperature:
        body["temperature"] = temperature

    if reasoning_effort and reasoning_effort.lower() != "none":
        body["reasoning"] = {"effort": reasoning_effort}
        body["include"] = ["reasoning.encrypted_content"]

    if tools:
        body["tools"] = convert_tools(tools)
        body["tool_choice"] = tool_choice or "auto"

    return body


async def create_response(client: Any, body: dict[str, Any]) -> LLMResponse:
    return parse_response_output(await client.responses.create(**body))


async def timed_stream(stream: Any, *, idle_timeout_s: int) -> AsyncIterator[Any]:
    stream_iter = stream.__aiter__()
    while True:
        try:
            yield await asyncio.wait_for(stream_iter.__anext__(), timeout=idle_timeout_s)
        except StopAsyncIteration:
            break


async def stream_response(
    client: Any,
    body: dict[str, Any],
    *,
    idle_timeout_s: int,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResponse:
    stream_body = dict(body)
    stream_body["stream"] = True
    stream = await client.responses.create(**stream_body)
    content, tool_calls, finish_reason, usage, reasoning_content = await consume_sdk_stream(
        timed_stream(stream, idle_timeout_s=idle_timeout_s),
        on_content_delta,
    )
    return LLMResponse(
        content=content or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        reasoning_content=reasoning_content,
    )
