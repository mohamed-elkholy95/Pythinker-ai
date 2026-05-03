"""Tests for the actionable error renderer (Phase 1 task 9)."""

from io import StringIO
from unittest.mock import patch

from pythinker.cli.onboard_views import clack
from pythinker.cli.onboard_views.errors import render_actionable


def _capture(fn, *args, **kwargs) -> str:
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        fn(*args, **kwargs)
    return buf.getvalue()


def test_render_actionable_emits_what_why_how_in_one_panel():
    """All three required fields appear in a single ``Error`` panel — the
    What/Why/How trio pythinker uses for every user-visible failure."""
    out = _capture(
        render_actionable,
        what="Could not write config",
        why="Wizard reached save step but FS refused",
        how="Check perms on ~/.pythinker and retry",
    )
    assert "Error" in out
    assert "What:" in out
    assert "Why:" in out
    assert "How:" in out
    assert "Could not write config" in out
    assert "Wizard reached save step" in out
    assert "Check perms" in out


def test_render_actionable_does_not_print_traceback():
    """The renderer must not surface internal traceback noise — that's the
    whole point of replacing bare ``traceback.format_exc`` with this helper."""
    out = _capture(
        render_actionable,
        what="X",
        why="Y",
        how="Z",
    )
    assert "Traceback" not in out
    assert ".py" not in out  # no source line refs
