"""GET /api/commands returns the palette rows for the WebUI; requires API token."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig


@pytest.fixture
def channel():
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765, token="t0k")
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = MagicMock()
    ch = WebSocketChannel(cfg, bus=bus, session_manager=sm)
    # Mint a valid api_token so _check_api_token returns True.
    import time
    ch._api_tokens["t0k"] = time.monotonic() + 60
    return ch


async def test_commands_route_returns_palette_rows(channel):
    request = MagicMock()
    request.headers = {"Authorization": "Bearer t0k"}
    request.path = "/api/commands"
    response = channel._handle_commands_list(request)
    assert response.status_code == 200
    import json
    body = json.loads(response.body.decode("utf-8"))
    assert "commands" in body
    names = [row["name"] for row in body["commands"]]
    # Sanity-check a few canonical entries.
    assert "/help" in names
    assert "/stop" in names
    assert "/dream-log" in names
    # Each row must carry name + summary; usage is optional.
    for row in body["commands"]:
        assert isinstance(row["name"], str)
        assert isinstance(row["summary"], str)
        assert "usage" in row  # always present, may be empty string


async def test_commands_route_requires_token(channel):
    request = MagicMock()
    request.headers = {}
    request.path = "/api/commands"
    response = channel._handle_commands_list(request)
    assert response.status_code == 401
