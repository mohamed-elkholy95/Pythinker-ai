"""Tests for the WebSocket channel's bind/auth safety rails.

Covers the hard-refusal cases on ``start()`` and the runtime gating that
prevents the SPA shell from leaking on a non-loopback bind without a
configured remote-auth path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel
from pythinker.session.manager import SessionManager


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


def _ch(bus_: Any, sm: SessionManager, **overrides: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 29960,
        "path": "/",
        "websocketRequiresToken": False,
    }
    cfg.update(overrides)
    return WebSocketChannel(cfg, bus_, session_manager=sm)


async def test_start_refuses_non_loopback_bind_without_tls(bus, tmp_path):
    """0.0.0.0 + no TLS + allow_insecure_remote=False must raise on start()."""
    sm = SessionManager(tmp_path)
    channel = _ch(bus, sm, host="0.0.0.0")
    with pytest.raises(RuntimeError, match="non-loopback host"):
        await channel.start()


async def test_start_allows_non_loopback_bind_with_explicit_opt_in(bus, tmp_path):
    """allow_insecure_remote=True bypasses the hard refusal (LAN-dev escape hatch)."""
    import asyncio

    sm = SessionManager(tmp_path)
    channel = _ch(bus, sm, host="0.0.0.0", port=29961, allowInsecureRemote=True)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    assert channel._running is True
    await channel.stop()
    await server_task


async def test_start_refuses_token_issue_path_without_secret_on_remote_bind(bus, tmp_path):
    """token_issue_path on a non-loopback host without secret must raise on start()."""
    sm = SessionManager(tmp_path)
    channel = _ch(
        bus,
        sm,
        host="0.0.0.0",
        port=29962,
        allowInsecureRemote=True,
        tokenIssuePath="/api/auth/token",
        tokenIssueSecret="",
    )
    with pytest.raises(RuntimeError, match="token_issue_path"):
        await channel.start()


def test_localhost_bind_helper_recognizes_loopback_aliases():
    """``_is_local_bind`` returns True for the canonical loopback hosts only."""
    from pythinker.channels.websocket import _is_local_bind

    assert _is_local_bind("127.0.0.1") is True
    assert _is_local_bind("::1") is True
    assert _is_local_bind("localhost") is True
    assert _is_local_bind("0.0.0.0") is False
    assert _is_local_bind("192.168.1.5") is False
    assert _is_local_bind("") is False
