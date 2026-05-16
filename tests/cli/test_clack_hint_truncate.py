"""Tests for ``clack._truncate_hint``.

Checkbox prompts still preserve questionary's completed answer line. If
that line wraps, the wrap continuation has no `│` left bar — visually
breaking clack's persistent timeline. Trimming checkbox hints to fit
terminal width keeps that record on one line.
"""

from __future__ import annotations

from unittest.mock import patch

from pythinker.cli.onboard_views.clack import _truncate_hint


def _at(width: int):
    return patch(
        "pythinker.cli.onboard_views.clack._terminal_width", return_value=width
    )


def test_short_hint_passes_unchanged():
    with _at(80):
        assert _truncate_hint("short", title="T", display="D") == "short"


def test_empty_hint_passes_unchanged():
    with _at(80):
        assert _truncate_hint("", title="T", display="D") == ""


def test_long_hint_is_ellipsized_to_fit_terminal():
    """At 70 columns, a title/display/hint combination that would wrap
    must come back ellipsized so the recorded checkbox answer line
    stays single-line."""
    with _at(70):
        out = _truncate_hint(
            "Load current config; refresh new schema fields.",
            title="What would you like to do?",
            display="Use existing",
        )
    # Final char is the ellipsis; line fits the budget.
    assert out.endswith("…")
    overhead = 3 + len("What would you like to do?") + 2 + len("Use existing") + 2
    assert overhead + len(out) <= 70


def test_minimum_budget_is_respected_in_extreme_cases():
    """Even if the title + display already overflow the terminal, the
    truncator must still leave a useful visible hint (18 budget minus the
    ellipsis char and any trailing whitespace stripped before the ellipsis
    appended). Better a wrap than a useless single-character hint."""
    with _at(40):
        out = _truncate_hint(
            "structured results", title="A very long title goes here", display="OptionDisplay"
        )
    visible = out.rstrip("…")
    assert len(visible) >= 16  # min-budget 18 minus ellipsis + possible rstrip


def test_uses_terminal_width_at_call_time(monkeypatch):
    """Truncation reads terminal_width on each call, so resizing between
    prompts is honored without restart."""
    with _at(120):
        wide = _truncate_hint("hint that is moderate length", title="t", display="d")
    with _at(40):
        narrow = _truncate_hint("hint that is moderate length", title="t", display="d")
    assert len(narrow) <= len(wide)
