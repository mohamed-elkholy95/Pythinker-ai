"""Shared helpers for OpenAI Responses API providers (Codex, Azure OpenAI)."""

from pythinker.providers.openai_responses.chat import (
    build_responses_body,
    create_response,
    is_direct_openai_base,
    stream_response,
    timed_stream,
)
from pythinker.providers.openai_responses.circuit import (
    _RESPONSES_FAILURE_THRESHOLD,
    _RESPONSES_PROBE_INTERVAL_S,
    circuit_allows_request,
    record_responses_failure,
    record_responses_success,
    responses_circuit_key,
    should_fallback_from_responses_error,
)
from pythinker.providers.openai_responses.converters import (
    convert_messages,
    convert_tools,
    convert_user_message,
    split_tool_call_id,
)
from pythinker.providers.openai_responses.parsing import (
    FINISH_REASON_MAP,
    consume_sdk_stream,
    consume_sse,
    iter_sse,
    map_finish_reason,
    parse_response_output,
)

__all__ = [
    "_RESPONSES_FAILURE_THRESHOLD",
    "_RESPONSES_PROBE_INTERVAL_S",
    "circuit_allows_request",
    "record_responses_failure",
    "record_responses_success",
    "responses_circuit_key",
    "should_fallback_from_responses_error",
    "build_responses_body",
    "create_response",
    "is_direct_openai_base",
    "stream_response",
    "timed_stream",
    "convert_messages",
    "convert_tools",
    "convert_user_message",
    "split_tool_call_id",
    "iter_sse",
    "consume_sse",
    "consume_sdk_stream",
    "map_finish_reason",
    "parse_response_output",
    "FINISH_REASON_MAP",
]
