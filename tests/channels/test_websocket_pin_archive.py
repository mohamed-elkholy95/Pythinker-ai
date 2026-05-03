"""GET /api/sessions/<key>/pin and /archive toggle and return new state."""
import time
from unittest.mock import MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig


@pytest.fixture
def channel():
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    sm = MagicMock()
    state = {"pinned": False, "archived": False, "title": "", "model_override": None}
    sm.read_meta = MagicMock(side_effect=lambda key: dict(state))

    def _write(key, **fields):
        state.update(fields)
        return dict(state)

    sm.write_meta = MagicMock(side_effect=_write)
    sm.read_session_file = MagicMock(return_value={"messages": []})
    ch = WebSocketChannel(cfg, bus=bus, session_manager=sm)
    ch._api_tokens["t0k"] = time.monotonic() + 300
    return ch, state


def _req(token: str | None):
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"} if token else {}
    request.path = "/api/sessions/websocket:abcd-1234/pin"
    return request


def test_pin_toggles_and_returns_new_state(channel):
    ch, state = channel
    r1 = ch._handle_session_pin(_req("t0k"), "websocket:abcd-1234")
    import json as _json
    assert _json.loads(r1.body)["pinned"] is True
    assert state["pinned"] is True
    r2 = ch._handle_session_pin(_req("t0k"), "websocket:abcd-1234")
    assert _json.loads(r2.body)["pinned"] is False
    assert state["pinned"] is False


def test_archive_toggles_and_returns_new_state(channel):
    ch, state = channel
    r1 = ch._handle_session_archive(_req("t0k"), "websocket:abcd-1234")
    import json as _json
    assert _json.loads(r1.body)["archived"] is True
    assert state["archived"] is True


def test_pin_requires_token(channel):
    ch, _ = channel
    r = ch._handle_session_pin(_req(None), "websocket:abcd-1234")
    assert r.status_code == 401


def test_pin_rejects_non_websocket_keys(channel):
    ch, _ = channel
    r = ch._handle_session_pin(_req("t0k"), "telegram_abcd")
    assert r.status_code == 404


def test_pin_rejects_missing_session(channel):
    ch, _ = channel
    ch._session_manager.read_session_file = MagicMock(return_value=None)
    r = ch._handle_session_pin(_req("t0k"), "websocket:does-not-exist")
    assert r.status_code == 404
