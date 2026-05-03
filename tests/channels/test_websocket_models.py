"""GET /api/models returns the dropdown rows for the WebUI model switcher."""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig
from pythinker.config.schema import AgentDefaults


@pytest.fixture
def channel():
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765, token="t0k")
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = MagicMock()
    defaults = AgentDefaults(
        model="anthropic/claude-3-5-sonnet-20241022",
        alternate_models=["anthropic/claude-3-5-haiku-20241022"],
    )
    ch = WebSocketChannel(cfg, bus=bus, session_manager=sm, agent_defaults=defaults)
    ch._api_tokens["t0k"] = time.monotonic() + 60
    return ch


def test_models_route_returns_current_plus_alternates(channel):
    request = MagicMock()
    request.headers = {"Authorization": "Bearer t0k"}
    request.path = "/api/models"
    response = channel._handle_models_list(request)
    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert "models" in body
    names = [row["name"] for row in body["models"]]
    assert names[0] == "anthropic/claude-3-5-sonnet-20241022"
    assert "anthropic/claude-3-5-haiku-20241022" in names
    assert any(row.get("is_default") for row in body["models"])


def test_models_route_requires_token(channel):
    request = MagicMock()
    request.headers = {}
    request.path = "/api/models"
    response = channel._handle_models_list(request)
    assert response.status_code == 401


def test_models_route_503_without_defaults(channel):
    channel._agent_defaults = None
    request = MagicMock()
    request.headers = {"Authorization": "Bearer t0k"}
    request.path = "/api/models"
    response = channel._handle_models_list(request)
    assert response.status_code == 503


def test_models_route_dedupes_alternate_matching_default(channel):
    """An alternate that duplicates the default model must not be emitted twice."""
    channel._agent_defaults = AgentDefaults(
        model="anthropic/claude-3-5-sonnet-20241022",
        alternate_models=[
            "anthropic/claude-3-5-sonnet-20241022",  # duplicate of default
            "anthropic/claude-3-5-haiku-20241022",
        ],
    )
    request = MagicMock()
    request.headers = {"Authorization": "Bearer t0k"}
    request.path = "/api/models"
    response = channel._handle_models_list(request)
    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    names = [row["name"] for row in body["models"]]
    assert names.count("anthropic/claude-3-5-sonnet-20241022") == 1
    assert "anthropic/claude-3-5-haiku-20241022" in names
