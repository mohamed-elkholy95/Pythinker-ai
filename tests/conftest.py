"""Shared fixtures for all Pythinker tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from pythinker.cli.onboard import _BACK_PRESSED


@pytest.fixture
def make_fake_select():
    """Factory: returns a _select_with_back stand-in that consumes a token list.

    Tokens:
      - "first" → first non-action choice in the list (skips bracketed actions
                  like "[Done]")
      - "done"  → "[Done]" (the commit sentinel used by _configure_pydantic_model)
      - "back"  → _BACK_PRESSED
      - "back-exit" → "<- Back" (the loop-exit string used in section pickers)
      - any other string → returned as-is (use this to pick a specific label)
    """

    def _factory(tokens):
        sequence = iter(tokens)

        def _fake(_prompt, choices, default=None):
            token = next(sequence)
            if token == "first":
                return next(c for c in choices if not c.strip().startswith("["))
            if token == "done":
                return "[Done]"
            if token == "back":
                return _BACK_PRESSED
            if token == "back-exit":
                return "<- Back"
            return token

        return _fake

    return _factory


@pytest.fixture(scope="module")
def browser_http_fixture():
    """Module-scoped: serves tests/fixtures/browser/ over HTTP on 0.0.0.0:<port>."""
    from tests.fixtures.browser.server import serve

    fixture_dir = Path(__file__).parent / "fixtures" / "browser"
    yield from serve(fixture_dir)
