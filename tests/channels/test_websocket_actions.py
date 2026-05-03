"""WebUI-driven action envelopes (stop / regenerate / edit).

These envelopes are intentionally thin: the channel's only job is to
validate the envelope shape and publish a priority slash command
(``/stop`` / ``/regenerate`` / ``/edit``) onto the inbound bus. The
actual session-state surgery happens in the agent loop's priority
command handler under the per-session lock — see
``pythinker/command/builtin.py``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig


@pytest.fixture
def channel() -> WebSocketChannel:
    cfg = WebSocketConfig(
        enabled=True,
        host="127.0.0.1",
        port=8765,
        websocket_requires_token=False,
        allow_from=["*"],
    )
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    return WebSocketChannel(cfg, bus=bus)


async def test_stop_envelope_publishes_priority_command(channel: WebSocketChannel) -> None:
    """A 'stop' envelope must enqueue '/stop' as a priority InboundMessage so the
    agent loop's priority router cancels the in-flight turn."""
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._attach(connection, "abcd-1234")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "stop", "chat_id": "abcd-1234"},
    )

    # Exactly one inbound message published, with /stop content.
    assert channel.bus.publish_inbound.await_count == 1
    msg = channel.bus.publish_inbound.await_args.args[0]
    assert msg.content == "/stop"
    assert msg.chat_id == "abcd-1234"
    assert msg.channel == "websocket"


async def test_regenerate_envelope_publishes_priority_command(
    channel: WebSocketChannel,
) -> None:
    """A 'regenerate' envelope must publish '/regenerate' as a priority
    InboundMessage so the agent loop's command handler can cancel the
    in-flight turn, await the per-session lock, then truncate and resend.

    The channel must NOT touch session state directly — that races with
    the agent loop's writer lock.
    """
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._attach(connection, "abcd-1234")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "regenerate", "chat_id": "abcd-1234"},
    )

    assert channel.bus.publish_inbound.await_count == 1
    msg = channel.bus.publish_inbound.await_args.args[0]
    assert msg.content == "/regenerate"
    assert msg.chat_id == "abcd-1234"
    assert msg.channel == "websocket"


async def test_edit_envelope_publishes_priority_command_with_metadata(
    channel: WebSocketChannel,
) -> None:
    """An 'edit' envelope publishes '/edit' with edit_* metadata for the
    agent loop's handler to consume under the per-session lock."""
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._attach(connection, "abcd-1234")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "edit",
            "chat_id": "abcd-1234",
            "user_msg_index": 0,
            "content": "new text",
        },
    )

    assert channel.bus.publish_inbound.await_count == 1
    msg = channel.bus.publish_inbound.await_args.args[0]
    assert msg.content == "/edit"
    assert msg.chat_id == "abcd-1234"
    assert msg.channel == "websocket"
    assert msg.metadata["edit_user_msg_index"] == 0
    assert msg.metadata["edit_content"] == "new text"


async def test_edit_envelope_rejects_empty_content(channel: WebSocketChannel) -> None:
    """Empty ``content`` on an edit envelope must be rejected with an error
    event, not silently published as a /edit priority command."""
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._attach(connection, "abcd-1234")

    # Capture the error event by stubbing _send_event.
    sent_events: list[tuple[str, dict]] = []

    async def _capture(conn, event_type, **kwargs):
        sent_events.append((event_type, kwargs))

    channel._send_event = _capture

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "edit",
            "chat_id": "abcd-1234",
            "user_msg_index": 0,
            "content": "   ",  # whitespace-only is empty after strip
        },
    )

    # No bus publish, error event sent.
    assert channel.bus.publish_inbound.await_count == 0
    assert any(
        e[0] == "error" and "empty" in e[1].get("detail", "")
        for e in sent_events
    ), f"expected empty-content error, got {sent_events}"
