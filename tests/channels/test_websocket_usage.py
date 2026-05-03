"""GET /api/sessions/<key>/usage returns {used, limit}; requires API token;
delegates token estimation to pythinker.agent.usage.estimate_session_usage."""
import json
from unittest.mock import AsyncMock, MagicMock

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig
from pythinker.config.schema import AgentDefaults


def _make_channel(messages):
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = MagicMock()
    # The handler is now a strict reader: it calls ``read_session_file`` and
    # 404s on ``None``. Returning the raw on-disk dict shape (with a
    # ``messages`` key) is enough — ``estimate_session_usage`` only reads
    # ``.messages`` on the shim the handler constructs.
    sm.read_session_file.return_value = {"messages": messages}
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)
    ch = WebSocketChannel(cfg, bus, session_manager=sm, agent_defaults=defaults)
    # The api-token check uses the channel's own _api_tokens dict — issue one.
    ch._api_tokens["valid-token"] = float("inf")
    return ch


def _request(token=None):
    """Build a fake WsRequest with optional Authorization header.

    `path` is set to a real string so `_check_api_token`'s query-param fallback
    (which does `"ws://x" + request.path`) doesn't blow up on a MagicMock.
    """
    request = MagicMock()
    request.path = "/"
    request.headers = {"Authorization": f"Bearer {token}"} if token else {}
    return request


def test_usage_route_returns_used_and_limit():
    """Authorized GET on a real session key returns the {used, limit} JSON shape."""
    ch = _make_channel(messages=[{"role": "user", "content": "hello"}])
    response = ch._handle_session_usage(
        _request(token="valid-token"), key="websocket:abcd-1234"
    )
    assert response.status_code == 200
    body = json.loads(response.body)
    assert "used" in body and "limit" in body
    assert body["limit"] == 128_000
    assert body["used"] >= 0


def test_usage_route_requires_token():
    ch = _make_channel(messages=[])
    response = ch._handle_session_usage(_request(), key="websocket:abcd-1234")
    assert response.status_code == 401


def test_usage_route_503_when_session_manager_missing():
    """Defensive guard: if session_manager somehow wasn't wired, surface 503."""
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    ch = WebSocketChannel(cfg, bus, session_manager=None, agent_defaults=None)
    ch._api_tokens["valid-token"] = float("inf")
    response = ch._handle_session_usage(
        _request(token="valid-token"), key="websocket:abcd-1234"
    )
    assert response.status_code == 503


def test_usage_route_rejects_non_webui_keys():
    """The webui session-key namespace is `websocket:`. Non-websocket keys 404."""
    ch = _make_channel(messages=[])
    response = ch._handle_session_usage(
        _request(token="valid-token"), key="telegram_5829880422"
    )
    assert response.status_code == 404


def test_usage_route_404_when_session_file_missing():
    """Reading a nonexistent session must 404 instead of silently creating it."""
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = MagicMock()
    sm.read_session_file.return_value = None  # the "doesn't exist" signal
    defaults = AgentDefaults(model="openai/gpt-4o-mini", context_window_tokens=128_000)
    ch = WebSocketChannel(cfg, bus, session_manager=sm, agent_defaults=defaults)
    ch._api_tokens["valid-token"] = float("inf")

    response = ch._handle_session_usage(
        _request(token="valid-token"), key="websocket:abcd-1234"
    )
    assert response.status_code == 404
    # CRITICAL: get_or_create must NOT have been called (no resurrection).
    sm.get_or_create.assert_not_called()
