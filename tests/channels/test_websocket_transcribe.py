"""``transcribe`` envelope decodes a base64 audio blob, hands it to the channel's
inherited ``transcribe_audio`` helper, and emits ``transcription_result`` back to the
originating connection.

The tests mock ``transcribe_audio`` on the channel instance so no real
OpenAI/Groq HTTP traffic is exercised.
"""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig
from pythinker.session.manager import Session


@pytest.fixture
def channel() -> WebSocketChannel:
    cfg = WebSocketConfig(enabled=True, host="127.0.0.1", port=8765)
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = MagicMock()
    sm.get_or_create.return_value = Session(key="websocket:abcd-1234")
    sm.save = MagicMock()
    ch = WebSocketChannel(cfg, bus=bus, session_manager=sm)
    # Simulate ChannelManager wiring (setattr after construction).
    ch.transcription_provider = "groq"
    ch.transcription_api_key = "test-key"
    ch.transcription_api_base = ""
    ch.transcription_language = None
    return ch


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def test_transcribe_happy_path_emits_result(channel: WebSocketChannel) -> None:
    """Valid envelope → decoded → transcribe_audio called → transcription_result event."""
    connection = MagicMock()
    connection.remote_address = ("127.0.0.1", 1234)
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock(return_value="hello world")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "audio_base64": _b64(b"fake-audio-bytes"),
            "format": "webm",
            "request_id": "req-1",
        },
    )

    channel.transcribe_audio.assert_awaited_once()
    # Tempfile path was passed and ends in the right suffix
    called_path = channel.transcribe_audio.await_args.args[0]
    assert str(called_path).endswith(".webm")

    channel._send_event.assert_awaited()
    args, kwargs = channel._send_event.call_args
    assert args[1] == "transcription_result"
    assert kwargs["text"] == "hello world"
    assert kwargs["request_id"] == "req-1"


async def test_transcribe_without_provider_emits_error(channel: WebSocketChannel) -> None:
    """No api_key configured → error event, never calls the provider."""
    channel.transcription_api_key = ""
    connection = MagicMock()
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock(return_value="should not run")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "audio_base64": _b64(b"x"),
            "format": "webm",
            "request_id": "req-2",
        },
    )

    channel.transcribe_audio.assert_not_awaited()
    args, kwargs = channel._send_event.call_args
    assert args[1] == "error"
    assert kwargs.get("detail") == "voice transcription not configured"
    assert kwargs.get("request_id") == "req-2"


async def test_transcribe_missing_audio_emits_error(channel: WebSocketChannel) -> None:
    connection = MagicMock()
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "format": "webm",
            "request_id": "req-3",
        },
    )

    channel.transcribe_audio.assert_not_awaited()
    args, kwargs = channel._send_event.call_args
    assert args[1] == "error"
    assert kwargs.get("detail") == "missing audio_base64"


async def test_transcribe_oversized_emits_error(channel: WebSocketChannel) -> None:
    """Decoded audio over 10 MiB → error, provider not called."""
    connection = MagicMock()
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock()
    # 10 MiB + 1 byte after decode
    big = b"\x00" * (10 * 1024 * 1024 + 1)

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "audio_base64": _b64(big),
            "format": "webm",
            "request_id": "req-4",
        },
    )

    channel.transcribe_audio.assert_not_awaited()
    args, kwargs = channel._send_event.call_args
    assert args[1] == "error"
    assert kwargs.get("detail") == "audio too large"


async def test_transcribe_bad_format_emits_error(channel: WebSocketChannel) -> None:
    connection = MagicMock()
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "audio_base64": _b64(b"x"),
            "format": "exe",
            "request_id": "req-5",
        },
    )

    channel.transcribe_audio.assert_not_awaited()
    args, kwargs = channel._send_event.call_args
    assert args[1] == "error"
    assert kwargs.get("detail") == "unsupported format"


async def test_transcribe_malformed_base64_emits_error(channel: WebSocketChannel) -> None:
    connection = MagicMock()
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "audio_base64": "not!valid!base64!@#",
            "format": "webm",
            "request_id": "req-6",
        },
    )

    channel.transcribe_audio.assert_not_awaited()
    args, kwargs = channel._send_event.call_args
    assert args[1] == "error"
    assert kwargs.get("detail") == "malformed audio_base64"


async def test_transcribe_provider_returns_empty_emits_error(
    channel: WebSocketChannel,
) -> None:
    """transcribe_audio swallows provider failures and returns "" — surface as error."""
    connection = MagicMock()
    channel._send_event = AsyncMock()
    channel.transcribe_audio = AsyncMock(return_value="")

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "transcribe",
            "audio_base64": _b64(b"audio"),
            "format": "wav",
            "request_id": "req-7",
        },
    )

    args, kwargs = channel._send_event.call_args
    assert args[1] == "error"
    assert kwargs.get("detail") == "transcription_failed"
    assert kwargs.get("request_id") == "req-7"
