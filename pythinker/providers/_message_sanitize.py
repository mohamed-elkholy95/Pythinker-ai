"""OpenAI-compatible request message sanitization helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import json_repair

from pythinker.providers.base import LLMProvider

ALLOWED_MSG_KEYS = frozenset({
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "name",
    "reasoning_content",
    "extra_content",
})


def normalize_tool_call_id(tool_call_id: Any) -> Any:
    """Normalize to a provider-safe 9-char alphanumeric form."""
    if not isinstance(tool_call_id, str):
        return tool_call_id
    if len(tool_call_id) == 9 and tool_call_id.isalnum():
        return tool_call_id
    return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]


def normalize_tool_call_arguments(arguments: Any) -> str:
    """Force function.arguments into a valid JSON object string."""
    if isinstance(arguments, str):
        stripped = arguments.strip()
        if not stripped:
            return "{}"
        try:
            parsed = json_repair.loads(stripped)
        except Exception:
            return "{}"
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False)
        return "{}"
    if isinstance(arguments, dict):
        return json.dumps(arguments, ensure_ascii=False)
    return "{}"


def sanitize_openai_messages(
    messages: list[dict[str, Any]],
    *,
    enforce_role_alternation: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Strip non-standard keys and normalize tool-call IDs/arguments."""
    sanitized = LLMProvider._sanitize_request_messages(messages, ALLOWED_MSG_KEYS)
    id_map: dict[str, str] = {}

    def map_id(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return id_map.setdefault(value, normalize_tool_call_id(value))

    for clean in sanitized:
        if isinstance(clean.get("tool_calls"), list):
            normalized = []
            for tc in clean["tool_calls"]:
                if not isinstance(tc, dict):
                    normalized.append(tc)
                    continue
                tc_clean = dict(tc)
                tc_clean["id"] = map_id(tc_clean.get("id"))
                function = tc_clean.get("function")
                if isinstance(function, dict):
                    function_clean = dict(function)
                    function_clean["arguments"] = normalize_tool_call_arguments(
                        function_clean.get("arguments")
                    )
                    tc_clean["function"] = function_clean
                normalized.append(tc_clean)
            clean["tool_calls"] = normalized
            if clean.get("role") == "assistant":
                # Some OpenAI-compatible gateways reject assistant messages
                # that mix non-empty content with tool_calls.
                clean["content"] = None
        if "tool_call_id" in clean and clean["tool_call_id"]:
            clean["tool_call_id"] = map_id(clean["tool_call_id"])
    return enforce_role_alternation(sanitized)
