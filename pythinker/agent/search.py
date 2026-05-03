"""Substring search across persisted session histories.

Scope: powers the WebUI's cross-chat search box. Naive O(n) substring scan
over the session iterator returned by
``SessionManager.iter_message_files_for_search``; sufficient for personal-use
deployments up to ~50k total messages across all sessions. Past that an FTS
index would help, but the brief explicitly favors the simpler path.

The helper is pure -- it accepts an iterable of ``(session_key, messages)``
pairs so the WebSocket handler can feed it the manager's generator and tests
can pass static fixtures.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict

# Hard cap on returned hits per request, regardless of the caller-provided
# ``limit``. Matches the brief's "first 200 hits" budget.
_HARD_LIMIT = 200


class SearchHit(TypedDict):
    session_key: str
    message_index: int
    role: str
    snippet: str
    # List of ``[start, end]`` half-open offsets into ``snippet`` where the
    # query matched. Multiple matches per snippet are common when a user
    # repeats the term within a 120-char window.
    match_offsets: list[list[int]]


def build_snippet(
    text: str,
    *,
    match_start: int,
    match_end: int,
    span: int = 120,
) -> tuple[str, list[list[int]]]:
    """Return a ``(snippet, match_offsets)`` pair centered on the match.

    Snippet is at most ``span`` characters plus leading/trailing ellipsis
    when the source is truncated. Subsequent matches inside the same snippet
    are also reported, so the caller's highlighter can render every one.
    """
    if not text:
        return "", []
    # Build a window of at most ``span`` characters centered on the match.
    # Leading/trailing ellipses (one char each) are added when the source
    # is truncated on that side, so the total snippet length is bounded by
    # ``span + 2``.
    needle_len = max(0, match_end - match_start)
    pad_total = max(0, span - needle_len)
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    raw_start = max(0, match_start - left_pad)
    raw_end = min(len(text), match_end + right_pad)
    # Re-balance if one side hit a boundary -- keeps the window full width
    # when possible (e.g. a match near the start should still show ``span``
    # chars, not ``span/2``).
    if raw_end - raw_start < span:
        if raw_start == 0:
            raw_end = min(len(text), raw_start + span)
        elif raw_end == len(text):
            raw_start = max(0, raw_end - span)
    raw = text[raw_start:raw_end]
    leading = "…" if raw_start > 0 else ""
    trailing = "…" if raw_end < len(text) else ""
    snippet = f"{leading}{raw}{trailing}"
    # Re-locate every occurrence of the matched substring in the snippet so
    # the frontend can highlight all of them.
    needle = text[match_start:match_end].lower()
    if not needle:
        return snippet, []
    haystack = snippet.lower()
    offsets: list[list[int]] = []
    cursor = 0
    while True:
        i = haystack.find(needle, cursor)
        if i < 0:
            break
        offsets.append([i, i + len(needle)])
        cursor = i + len(needle)
    return snippet, offsets


def search_sessions(
    sessions: Iterable[tuple[str, list[dict[str, Any]]]],
    *,
    query: str,
    limit: int = 50,
    offset: int = 0,
) -> list[SearchHit]:
    """Run a case-insensitive substring search across every session.

    Iteration order matches the input iterator's order. The caller decides
    whether to sort by recency upstream (e.g. by reading
    ``SessionManager.list_sessions`` first and feeding the iter in that
    order). ``limit`` is clamped to ``_HARD_LIMIT`` (200) regardless of the
    caller's request.
    """
    if not query:
        return []
    needle = query.lower()
    cap = max(0, min(limit, _HARD_LIMIT))
    skip = max(0, offset)
    results: list[SearchHit] = []
    seen = 0
    for session_key, messages in sessions:
        for idx, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue
            haystack = content.lower()
            i = haystack.find(needle)
            if i < 0:
                continue
            seen += 1
            if seen <= skip:
                continue
            snippet, match_offsets = build_snippet(
                content,
                match_start=i,
                match_end=i + len(needle),
            )
            role = str(msg.get("role") or "")
            results.append(
                {
                    "session_key": session_key,
                    "message_index": idx,
                    "role": role,
                    "snippet": snippet,
                    "match_offsets": match_offsets,
                }
            )
            if len(results) >= cap:
                return results
    return results
