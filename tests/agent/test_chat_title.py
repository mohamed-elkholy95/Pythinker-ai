"""Tests for chat-title generation cleanup.

The cleaner runs after the model responds, so reasoning models that prepend
``<think>...</think>`` chain-of-thought must not have those blocks persisted
as the visible chat title.
"""

from __future__ import annotations

from pythinker.agent.chat_title import _clean_title


def test_clean_title_strips_think_block_then_uses_first_line():
    raw = "<think>The user wants a concise chat title.</think>\nGitHub trending repos"
    assert _clean_title(raw) == "GitHub trending repos"


def test_clean_title_strips_inline_think_block():
    raw = "<think>reasoning here</think>Browser tool questions"
    assert _clean_title(raw) == "Browser tool questions"


def test_clean_title_handles_unclosed_think_prefix():
    # Streamed responses may close mid-thought; helpers.strip_think drops the
    # whole "<think>...EOF" tail, leaving an empty string.
    raw = "<think>I think the title should be"
    assert _clean_title(raw) == ""


def test_clean_title_pure_think_returns_empty_string():
    raw = "<think>Just thinking, no answer.</think>"
    assert _clean_title(raw) == ""


def test_clean_title_strips_quotes_and_trailing_punctuation():
    assert _clean_title('"Browser trending question."') == "Browser trending question"


def test_clean_title_handles_empty_input():
    assert _clean_title("") == ""
    assert _clean_title("   \n  ") == ""


def test_clean_title_truncates_long_output():
    raw = "x" * 80
    cleaned = _clean_title(raw)
    assert len(cleaned) <= 60
    assert cleaned.endswith("…")
