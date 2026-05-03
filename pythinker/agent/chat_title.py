"""Generate a short, context-aware chat title from a conversation turn.

Used by the agent loop to label freshly-created webui sessions in the
sidebar (instead of falling back to "New chat" / the raw first user
message).  Designed to fail silently — title generation is best-effort
metadata, never blocking the main agent flow.
"""

from __future__ import annotations

from loguru import logger

from pythinker.providers.base import LLMProvider

_TITLE_PROMPT = (
    "Read the following short conversation and produce a concise chat title "
    "(4 to 6 words, no surrounding quotes, no trailing punctuation, no emoji). "
    "The title should describe what the user is asking about — not generic "
    "words like 'conversation', 'chat', or 'help'.\n\n"
    "User: {user}\n"
    "Assistant: {assistant}\n\n"
    "Title:"
)

_MAX_INPUT_CHARS = 600
_MAX_OUTPUT_CHARS = 60


def _truncate(text: str, n: int) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _clean_title(raw: str) -> str:
    """Strip quotes, trailing punctuation, and excess whitespace from a title."""
    title = raw.strip().splitlines()[0] if raw else ""
    title = title.strip().strip("\"'`“”‘’ ").rstrip(".!?,:;").strip()
    if len(title) > _MAX_OUTPUT_CHARS:
        title = title[: _MAX_OUTPUT_CHARS - 1] + "…"
    return title


async def generate_title(
    provider: LLMProvider,
    user_text: str,
    assistant_text: str,
) -> str:
    """Ask the configured LLM for a short title summarizing the turn.

    Returns an empty string on any failure (network, malformed response,
    unconfigured model). Never raises — title generation is best-effort.
    """
    user = _truncate(user_text or "", _MAX_INPUT_CHARS)
    assistant = _truncate(assistant_text or "", _MAX_INPUT_CHARS)
    if not user:
        return ""

    prompt = _TITLE_PROMPT.format(user=user, assistant=assistant)
    try:
        response = await provider.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            max_tokens=32,
            temperature=0.2,
        )
    except Exception as e:
        logger.debug(f"chat-title generation failed: {e}")
        return ""

    raw = (response.content or "").strip()
    return _clean_title(raw)
