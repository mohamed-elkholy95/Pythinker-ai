"""Tests for BrowserContextState and the SSRF route handler."""

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pythinker.agent.browser.state import (
    BrowserContextState,
    _ssrf_route_handler,
    storage_path_for_key,
)


def test_storage_path_uses_sha256_prefix(tmp_path):
    key = "telegram:42"
    expected_name = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] + ".json"
    assert storage_path_for_key(tmp_path, key).name == expected_name


def test_storage_path_no_filesystem_illegal_chars(tmp_path):
    """Effective keys can contain ':' and '/' — those must not leak into filenames."""
    path = storage_path_for_key(tmp_path, "weird:chat/id with spaces")
    assert path.parent == tmp_path
    assert "/" not in path.name and ":" not in path.name and " " not in path.name
    assert path.suffix == ".json"


async def test_save_storage_state_writes_json(tmp_path):
    storage = tmp_path / "x.json"
    fake_ctx = MagicMock()
    fake_ctx.storage_state = AsyncMock(return_value={"cookies": [{"name": "k", "value": "v"}]})
    state = BrowserContextState(
        effective_key="k",
        context=fake_ctx,
        page=MagicMock(),
        storage_path=storage,
        lock=asyncio.Lock(),
        last_used_at=0.0,
    )
    await state.save_storage_state()
    assert storage.exists()
    assert json.loads(storage.read_text())["cookies"][0]["name"] == "k"


async def test_save_storage_state_swallows_dead_context(tmp_path):
    """If the underlying context is closed, save must not raise — log + skip."""
    storage = tmp_path / "x.json"
    fake_ctx = MagicMock()
    fake_ctx.storage_state = AsyncMock(side_effect=RuntimeError("context closed"))
    state = BrowserContextState(
        effective_key="k",
        context=fake_ctx,
        page=MagicMock(),
        storage_path=storage,
        lock=asyncio.Lock(),
        last_used_at=0.0,
    )
    await state.save_storage_state()  # must not raise
    assert not storage.exists()


async def test_ssrf_route_blocks_private_ip():
    state = BrowserContextState(
        effective_key="k",
        context=MagicMock(),
        page=MagicMock(),
        storage_path=Path("/tmp/x.json"),
        lock=asyncio.Lock(),
        last_used_at=0.0,
    )
    fake_route = MagicMock()
    fake_route.request = MagicMock(url="http://10.0.0.1/")
    fake_route.abort = AsyncMock()
    fake_route.continue_ = AsyncMock()
    handler = _ssrf_route_handler(state)
    await handler(fake_route)
    fake_route.abort.assert_awaited_once()
    fake_route.continue_.assert_not_awaited()
    assert state.blocked_this_action == 1


async def test_ssrf_route_allows_public_url():
    state = BrowserContextState(
        effective_key="k",
        context=MagicMock(),
        page=MagicMock(),
        storage_path=Path("/tmp/x.json"),
        lock=asyncio.Lock(),
        last_used_at=0.0,
    )
    fake_route = MagicMock()
    fake_route.request = MagicMock(url="https://example.com/")
    fake_route.abort = AsyncMock()
    fake_route.continue_ = AsyncMock()
    handler = _ssrf_route_handler(state)
    await handler(fake_route)
    fake_route.continue_.assert_awaited_once()
    fake_route.abort.assert_not_awaited()
    assert state.blocked_this_action == 0
