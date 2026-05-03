"""GET /api/search?q=...&offset=...&limit=... returns paginated hit list."""
import json
from unittest.mock import MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig
from pythinker.config.schema import AgentDefaults


@pytest.fixture
def channel():
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    sm = MagicMock()
    sm.iter_message_files_for_search.return_value = iter([
        ("websocket:abcd-1", [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "Hello, how can I help?"},
        ]),
        ("websocket:efgh-2", [
            {"role": "user", "content": "no match here"},
        ]),
    ])
    sm.read_meta.return_value = {
        "title": "", "pinned": False, "archived": False, "model_override": None,
    }
    ch = WebSocketChannel(
        cfg, bus=bus, session_manager=sm, agent_defaults=AgentDefaults()
    )
    # Pre-seed an API token so the auth check passes.
    import time as _t
    ch._api_tokens["t0k"] = _t.monotonic() + 300
    return ch


def _request(token, query="hello", offset=0, limit=50):
    request = MagicMock()
    request.path = f"/api/search?q={query}&offset={offset}&limit={limit}"
    request.headers = {"Authorization": f"Bearer {token}"} if token else {}
    return request


def test_search_returns_results_with_pagination_envelope(channel):
    response = channel._handle_search(_request("t0k", query="hello"))
    assert response.status_code == 200
    body = json.loads(response.body)
    assert "results" in body
    assert "offset" in body
    assert "limit" in body
    assert "has_more" in body
    assert len(body["results"]) == 2  # both messages contain "hello"
    first = body["results"][0]
    assert first["session_key"] == "websocket:abcd-1"
    assert first["message_index"] == 0
    assert first["role"] == "user"
    assert "match_offsets" in first
    assert "archived" in first  # row-level archived flag for chip rendering


def test_search_requires_token(channel):
    response = channel._handle_search(_request(None))
    assert response.status_code == 401


def test_search_clamps_limit(channel):
    """Caller-supplied limit > 200 is silently clamped."""
    response = channel._handle_search(_request("t0k", limit=10_000))
    body = json.loads(response.body)
    assert body["limit"] <= 200


def test_search_empty_query_returns_empty_results(channel):
    response = channel._handle_search(_request("t0k", query=""))
    body = json.loads(response.body)
    assert body["results"] == []
