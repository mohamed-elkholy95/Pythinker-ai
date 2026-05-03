"""Tests for the embedded webui's /webui/bootstrap response shape.

The bootstrap response carries a ``voice_enabled`` flag the frontend uses to
decide whether the mic button is interactive. It is derived from whether a
transcription provider name and API key have been wired onto the channel by
``ChannelManager``.
"""

import asyncio
import functools
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pythinker.channels.websocket import WebSocketChannel
from pythinker.session.manager import Session, SessionManager

_PORT = 29950
_PORT_VOICE_ON = 29951


def _ch(bus: Any, *, session_manager: SessionManager, port: int) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port,
        "path": "/",
        "websocketRequiresToken": False,
    }
    return WebSocketChannel(cfg, bus, session_manager=session_manager)


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


async def _http_get(url: str) -> httpx.Response:
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, timeout=5.0)
    )


def _seed_session(workspace: Path) -> SessionManager:
    sm = SessionManager(workspace)
    s = Session(key="websocket:test")
    s.add_message("user", "hi")
    sm.save(s)
    return sm


@pytest.mark.asyncio
async def test_bootstrap_voice_enabled_false_without_transcription_key(
    bus: MagicMock, tmp_path: Path
) -> None:
    """voice_enabled is False when no transcription api key is configured."""
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=_PORT)
    # Default state: provider name is "groq" but api key is empty → False.
    assert channel.transcription_api_key == ""
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get(f"http://127.0.0.1:{_PORT}/webui/bootstrap")
        assert resp.status_code == 200
        body = resp.json()
        assert "voice_enabled" in body, "bootstrap must advertise voice_enabled"
        assert body["voice_enabled"] is False
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_bootstrap_voice_enabled_true_when_provider_configured(
    bus: MagicMock, tmp_path: Path
) -> None:
    """voice_enabled flips to True once provider + api key are wired."""
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=_PORT_VOICE_ON)
    # Simulate ChannelManager wiring (setattr after construction).
    channel.transcription_provider = "groq"
    channel.transcription_api_key = "fake-test-key"
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get(f"http://127.0.0.1:{_PORT_VOICE_ON}/webui/bootstrap")
        assert resp.status_code == 200
        body = resp.json()
        assert body["voice_enabled"] is True
    finally:
        await channel.stop()
        await server_task
