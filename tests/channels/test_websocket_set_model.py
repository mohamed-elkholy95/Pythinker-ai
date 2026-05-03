"""set_model envelope persists the per-chat override on Session.metadata."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig
from pythinker.session.manager import Session


@pytest.fixture
def channel():
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = MagicMock()
    sm.get_or_create.return_value = Session(key="websocket:abcd-1234")
    sm.save = MagicMock()
    ch = WebSocketChannel(cfg, bus=bus, session_manager=sm)
    return ch


async def test_set_model_writes_session_metadata(channel):
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._attach(connection, "abcd-1234")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "set_model",
            "chat_id": "abcd-1234",
            "model": "anthropic/claude-3-5-haiku-20241022",
        },
    )

    sm = channel._session_manager
    sm.get_or_create.assert_called_with("websocket:abcd-1234")
    saved_session = sm.save.call_args.args[0]
    assert saved_session.metadata["model_override"] == (
        "anthropic/claude-3-5-haiku-20241022"
    )


async def test_set_model_with_empty_string_clears_override(channel):
    """Sending {model: ""} removes the override (revert to default)."""
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._attach(connection, "abcd-1234")

    # Pre-seed an override so we can assert removal.
    seeded = Session(key="websocket:abcd-1234")
    seeded.metadata["model_override"] = "anthropic/claude-3-5-haiku-20241022"
    channel._session_manager.get_or_create.return_value = seeded

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "set_model", "chat_id": "abcd-1234", "model": ""},
    )

    saved_session = channel._session_manager.save.call_args.args[0]
    assert "model_override" not in saved_session.metadata


async def test_set_model_invalid_envelope_emits_error(channel):
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "set_model", "chat_id": "bad chat id", "model": "x"},
    )
    channel._send_event.assert_awaited()
    args, kwargs = channel._send_event.call_args
    assert kwargs.get("detail") == "invalid chat_id"
