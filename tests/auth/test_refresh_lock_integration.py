"""Integration tests proving refresh_lock is actually wired into provider refresh paths.

Companion to ``tests/auth/test_refresh_lock.py`` which tests the lock primitive in
isolation. These tests exercise the production call-sites in
``pythinker/providers/openai_codex_provider.py`` and
``pythinker/providers/github_copilot_provider.py`` to make sure the cross-process
serialization is actually held during token refreshes (B-6).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fcntl.flock not available on Windows; refresh_lock is best-effort there",
)
async def test_codex_refresh_serialized_under_concurrent_calls():
    """Two concurrent ``_locked_get_codex_token`` calls must serialize on the flock.

    Without the lock, both calls would enter ``get_codex_token`` overlapping; with
    the lock the second call waits for the first to release before proceeding.
    """
    from pythinker.providers import openai_codex_provider as mod

    events: list[tuple[str, float]] = []
    lock = threading.Lock()
    counter = {"n": 0}

    def fake_get_codex_token():
        with lock:
            counter["n"] += 1
            label = f"call-{counter['n']}"
        events.append((f"{label}-enter", time.monotonic()))
        time.sleep(0.1)
        events.append((f"{label}-exit", time.monotonic()))
        return SimpleNamespace(account_id="acct", access="token")

    with patch.object(mod, "get_codex_token", side_effect=fake_get_codex_token):
        await asyncio.gather(
            asyncio.to_thread(mod._locked_get_codex_token),
            asyncio.to_thread(mod._locked_get_codex_token),
        )

    # Four events recorded: two enters and two exits.
    assert len(events) == 4
    names = [name for name, _ in events]

    # Critical sections must not overlap. The legal interleavings are:
    #   enter-A, exit-A, enter-B, exit-B   (or A/B swapped)
    # Anything like enter-A, enter-B, ... means the lock leaked.
    assert names[0].endswith("-enter")
    assert names[1].endswith("-exit")
    assert names[2].endswith("-enter")
    assert names[3].endswith("-exit")
    first = names[0].split("-enter")[0]
    second = names[2].split("-enter")[0]
    assert first != second, "Same call appears twice; events corrupt"
    # The call that entered first must also be the one that exits first.
    assert names[1] == f"{first}-exit"
    assert names[3] == f"{second}-exit"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fcntl.flock not available on Windows; refresh_lock is best-effort there",
)
async def test_copilot_refresh_serialized_under_concurrent_calls():
    """Two concurrent Copilot token-exchange calls must serialize on the flock."""
    from pythinker.providers import github_copilot_provider as mod

    events: list[tuple[str, float]] = []
    lock = threading.Lock()
    counter = {"n": 0}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"token": "ghu_fake", "refresh_in": 1500}

    class _FakeHTTPClient:
        def __init__(self, *_, **__):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *_, **__):
            with lock:
                counter["n"] += 1
                label = f"call-{counter['n']}"
            events.append((f"{label}-enter", time.monotonic()))
            time.sleep(0.1)
            events.append((f"{label}-exit", time.monotonic()))
            return _FakeResponse()

    with patch.object(mod.httpx, "Client", _FakeHTTPClient):
        await asyncio.gather(
            asyncio.to_thread(mod._locked_exchange_copilot_token, "gho_fake"),
            asyncio.to_thread(mod._locked_exchange_copilot_token, "gho_fake"),
        )

    assert len(events) == 4
    names = [name for name, _ in events]
    assert names[0].endswith("-enter")
    assert names[1].endswith("-exit")
    assert names[2].endswith("-enter")
    assert names[3].endswith("-exit")
    first = names[0].split("-enter")[0]
    second = names[2].split("-enter")[0]
    assert first != second
    assert names[1] == f"{first}-exit"
    assert names[3] == f"{second}-exit"
