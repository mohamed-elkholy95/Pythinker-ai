"""Tests for WhatsApp channel outbound media support."""

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.bus.events import OutboundMessage
from pythinker.channels.whatsapp import (
    WhatsAppChannel,
    _ensure_bridge_setup,
    _load_or_create_bridge_token,
)


def _make_channel() -> WhatsAppChannel:
    bus = MagicMock()
    ch = WhatsAppChannel({"enabled": True}, bus)
    ch._ws = AsyncMock()
    ch._connected = True
    return ch


@pytest.mark.asyncio
async def test_send_text_only():
    ch = _make_channel()
    msg = OutboundMessage(channel="whatsapp", chat_id="123@s.whatsapp.net", content="hello")

    await ch.send(msg)

    ch._ws.send.assert_called_once()
    payload = json.loads(ch._ws.send.call_args[0][0])
    assert payload["type"] == "send"
    assert payload["text"] == "hello"


@pytest.mark.asyncio
async def test_send_media_dispatches_send_media_command():
    ch = _make_channel()
    msg = OutboundMessage(
        channel="whatsapp",
        chat_id="123@s.whatsapp.net",
        content="check this out",
        media=["/tmp/photo.jpg"],
    )

    await ch.send(msg)

    assert ch._ws.send.call_count == 2
    text_payload = json.loads(ch._ws.send.call_args_list[0][0][0])
    media_payload = json.loads(ch._ws.send.call_args_list[1][0][0])

    assert text_payload["type"] == "send"
    assert text_payload["text"] == "check this out"

    assert media_payload["type"] == "send_media"
    assert media_payload["filePath"] == "/tmp/photo.jpg"
    assert media_payload["mimetype"] == "image/jpeg"
    assert media_payload["fileName"] == "photo.jpg"


@pytest.mark.asyncio
async def test_send_media_only_no_text():
    ch = _make_channel()
    msg = OutboundMessage(
        channel="whatsapp",
        chat_id="123@s.whatsapp.net",
        content="",
        media=["/tmp/doc.pdf"],
    )

    await ch.send(msg)

    ch._ws.send.assert_called_once()
    payload = json.loads(ch._ws.send.call_args[0][0])
    assert payload["type"] == "send_media"
    assert payload["mimetype"] == "application/pdf"


@pytest.mark.asyncio
async def test_send_multiple_media():
    ch = _make_channel()
    msg = OutboundMessage(
        channel="whatsapp",
        chat_id="123@s.whatsapp.net",
        content="",
        media=["/tmp/a.png", "/tmp/b.mp4"],
    )

    await ch.send(msg)

    assert ch._ws.send.call_count == 2
    p1 = json.loads(ch._ws.send.call_args_list[0][0][0])
    p2 = json.loads(ch._ws.send.call_args_list[1][0][0])
    assert p1["mimetype"] == "image/png"
    assert p2["mimetype"] == "video/mp4"


@pytest.mark.asyncio
async def test_send_when_disconnected_is_noop():
    ch = _make_channel()
    ch._connected = False

    msg = OutboundMessage(
        channel="whatsapp",
        chat_id="123@s.whatsapp.net",
        content="hello",
        media=["/tmp/x.jpg"],
    )
    await ch.send(msg)

    ch._ws.send.assert_not_called()


@pytest.mark.asyncio
async def test_set_presence_sends_presence_command():
    ch = _make_channel()

    await ch.set_presence("123@s.whatsapp.net", "composing")

    ch._ws.send.assert_called_once()
    payload = json.loads(ch._ws.send.call_args[0][0])
    assert payload == {
        "type": "presence",
        "to": "123@s.whatsapp.net",
        "state": "composing",
    }


@pytest.mark.asyncio
async def test_send_clears_typing_task_and_pauses_before_reply():
    ch = _make_channel()
    chat_id = "123@s.whatsapp.net"
    typing_task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[chat_id] = typing_task

    await ch.send(OutboundMessage(channel="whatsapp", chat_id=chat_id, content="hello"))

    assert typing_task.cancelled()
    assert chat_id not in ch._typing_tasks
    assert ch._ws.send.call_count == 2
    paused_payload = json.loads(ch._ws.send.call_args_list[0][0][0])
    send_payload = json.loads(ch._ws.send.call_args_list[1][0][0])
    assert paused_payload == {"type": "presence", "to": chat_id, "state": "paused"}
    assert send_payload["type"] == "send"


@pytest.mark.asyncio
async def test_typing_loop_stops_on_unexpected_error():
    ch = _make_channel()

    async def raise_presence(chat_id: str, state: str) -> None:
        raise RuntimeError("presence failed")

    ch.set_presence = raise_presence

    await ch._typing_loop("123@s.whatsapp.net")


@pytest.mark.asyncio
async def test_progress_message_does_not_clear_typing_task():
    ch = _make_channel()
    chat_id = "123@s.whatsapp.net"
    typing_task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[chat_id] = typing_task

    await ch.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id=chat_id,
            content="Working...",
            metadata={"_progress": True},
        )
    )

    assert ch._typing_tasks[chat_id] is typing_task
    assert not typing_task.done()
    payload = json.loads(ch._ws.send.call_args[0][0])
    assert payload["type"] == "send"
    typing_task.cancel()
    await asyncio.gather(typing_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_disconnected_status_cancels_typing_tasks():
    ch = _make_channel()
    chat_id = "123@s.whatsapp.net"
    typing_task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[chat_id] = typing_task

    await ch._handle_bridge_message(json.dumps({"type": "status", "status": "disconnected"}))

    assert not ch._connected
    assert typing_task.cancelled()
    assert ch._typing_tasks == {}


@pytest.mark.asyncio
async def test_group_policy_mention_skips_unmentioned_group_message():
    ch = WhatsAppChannel({"enabled": True, "groupPolicy": "mention"}, MagicMock())
    ch._handle_message = AsyncMock()

    await ch._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m1",
                "sender": "12345@g.us",
                "pn": "user@s.whatsapp.net",
                "content": "hello group",
                "timestamp": 1,
                "isGroup": True,
                "wasMentioned": False,
            }
        )
    )

    ch._handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_group_policy_mention_accepts_mentioned_group_message():
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"], "groupPolicy": "mention"}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    await ch._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m1",
                "sender": "12345@g.us",
                "pn": "user@s.whatsapp.net",
                "content": "hello @bot",
                "timestamp": 1,
                "isGroup": True,
                "wasMentioned": True,
            }
        )
    )

    ch._handle_message.assert_awaited_once()
    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["chat_id"] == "12345@g.us"
    assert kwargs["sender_id"] == "user"
    assert "12345@g.us" in ch._typing_tasks
    await ch._cancel_all_typing_tasks()


@pytest.mark.asyncio
async def test_duplicate_message_does_not_start_second_typing_task():
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"]}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True
    started = 0

    async def fake_typing_loop(chat_id: str) -> None:
        nonlocal started
        started += 1
        await asyncio.Event().wait()

    ch._typing_loop = fake_typing_loop
    message = json.dumps(
        {
            "type": "message",
            "id": "dup1",
            "sender": "12345@s.whatsapp.net",
            "pn": "",
            "content": "hello",
            "timestamp": 1,
        }
    )

    await ch._handle_bridge_message(message)
    await asyncio.sleep(0)
    await ch._handle_bridge_message(message)
    await asyncio.sleep(0)

    assert started == 1
    ch._handle_message.assert_awaited_once()
    await ch._cancel_all_typing_tasks()


@pytest.mark.asyncio
async def test_sender_id_prefers_phone_jid_over_lid():
    """sender_id should resolve to phone number when @s.whatsapp.net JID is present."""
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"]}, MagicMock())
    ch._handle_message = AsyncMock()

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "lid1",
            "sender": "ABC123@lid.whatsapp.net",
            "pn": "5551234@s.whatsapp.net",
            "content": "hi",
            "timestamp": 1,
        })
    )

    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["sender_id"] == "5551234"


@pytest.mark.asyncio
async def test_lid_to_phone_cache_resolves_lid_only_messages():
    """When only LID is present, a cached LID→phone mapping should be used."""
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"]}, MagicMock())
    ch._handle_message = AsyncMock()

    # First message: both phone and LID → builds cache
    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "c1",
            "sender": "LID99@lid.whatsapp.net",
            "pn": "5559999@s.whatsapp.net",
            "content": "first",
            "timestamp": 1,
        })
    )
    # Second message: only LID, no phone
    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "c2",
            "sender": "LID99@lid.whatsapp.net",
            "pn": "",
            "content": "second",
            "timestamp": 2,
        })
    )

    second_kwargs = ch._handle_message.await_args_list[1].kwargs
    assert second_kwargs["sender_id"] == "5559999"


@pytest.mark.asyncio
async def test_unauthorized_sender_is_rejected_before_transcription():
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["5550000"]}, MagicMock())
    ch._handle_message = AsyncMock()
    ch.transcribe_audio = AsyncMock(return_value="secret")

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "blocked-voice",
            "sender": "12345@s.whatsapp.net",
            "pn": "",
            "content": "[Voice Message]",
            "timestamp": 1,
            "media": ["/tmp/voice.ogg"],
        })
    )

    ch.transcribe_audio.assert_not_awaited()
    ch._handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_voice_message_transcription_uses_media_path():
    """Voice messages are transcribed when media path is available."""
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"]}, MagicMock())
    ch.transcription_provider = "openai"
    ch.transcription_api_key = "sk-test"
    ch._handle_message = AsyncMock()
    ch.transcribe_audio = AsyncMock(return_value="Hello world")

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "v1",
            "sender": "12345@s.whatsapp.net",
            "pn": "",
            "content": "[Voice Message]",
            "timestamp": 1,
            "media": ["/tmp/voice.ogg"],
        })
    )

    ch.transcribe_audio.assert_awaited_once_with("/tmp/voice.ogg")
    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["content"].startswith("Hello world")
    # Regression: the .ogg path must not leak into content as `[file: ...]`
    # or into the outbound `media` list — otherwise the LLM sees an audio
    # attachment alongside the transcription and replies "cannot process
    # audio" despite the transcription having succeeded.
    assert "[file:" not in kwargs["content"]
    assert "voice.ogg" not in kwargs["content"]
    assert kwargs["media"] == []


@pytest.mark.asyncio
async def test_voice_message_no_media_shows_not_available():
    """Voice messages without media produce a fallback placeholder."""
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"]}, MagicMock())
    ch._handle_message = AsyncMock()

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "v2",
            "sender": "12345@s.whatsapp.net",
            "pn": "",
            "content": "[Voice Message]",
            "timestamp": 1,
        })
    )

    kwargs = ch._handle_message.await_args.kwargs
    assert kwargs["content"] == "[Voice Message: Audio not available]"


def test_load_or_create_bridge_token_persists_generated_secret(tmp_path):
    token_path = tmp_path / "whatsapp-auth" / "bridge-token"

    first = _load_or_create_bridge_token(token_path)
    second = _load_or_create_bridge_token(token_path)

    assert first == second
    assert token_path.read_text(encoding="utf-8") == first
    assert len(first) >= 32
    if os.name != "nt":
        assert token_path.stat().st_mode & 0o777 == 0o600


def test_configured_bridge_token_skips_local_token_file(monkeypatch, tmp_path):
    token_path = tmp_path / "whatsapp-auth" / "bridge-token"
    monkeypatch.setattr("pythinker.channels.whatsapp._bridge_token_path", lambda: token_path)
    ch = WhatsAppChannel({"enabled": True, "bridgeToken": "manual-secret"}, MagicMock())

    assert ch._effective_bridge_token() == "manual-secret"
    assert not token_path.exists()


def test_ensure_bridge_setup_uses_dev_source_dir(monkeypatch, tmp_path):
    dev_dir = tmp_path / "bridge"
    (dev_dir / "dist").mkdir(parents=True)
    (dev_dir / "package.json").write_text("{}", encoding="utf-8")
    (dev_dir / "dist" / "index.js").write_text("// stub", encoding="utf-8")
    monkeypatch.setenv("PYTHINKER_BRIDGE_SOURCE_DIR", str(dev_dir))

    assert _ensure_bridge_setup() == dev_dir.resolve()


def test_ensure_bridge_setup_dev_dir_missing_dist_errors(monkeypatch, tmp_path):
    dev_dir = tmp_path / "bridge"
    dev_dir.mkdir()
    (dev_dir / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PYTHINKER_BRIDGE_SOURCE_DIR", str(dev_dir))

    with pytest.raises(RuntimeError, match="dist/index.js missing"):
        _ensure_bridge_setup()


def test_ensure_bridge_setup_dev_dir_missing_package_json_errors(monkeypatch, tmp_path):
    dev_dir = tmp_path / "bridge"
    dev_dir.mkdir()
    monkeypatch.setenv("PYTHINKER_BRIDGE_SOURCE_DIR", str(dev_dir))

    with pytest.raises(RuntimeError, match="no package.json"):
        _ensure_bridge_setup()


@pytest.mark.asyncio
async def test_login_exports_effective_bridge_token(monkeypatch, tmp_path):
    token_path = tmp_path / "whatsapp-auth" / "bridge-token"
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    calls = []

    monkeypatch.setattr("pythinker.channels.whatsapp._bridge_token_path", lambda: token_path)
    monkeypatch.setattr("pythinker.channels.whatsapp._ensure_bridge_setup", lambda: bridge_dir)
    monkeypatch.setattr("pythinker.channels.whatsapp.shutil.which", lambda _: "/usr/bin/npm")

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return MagicMock()

    monkeypatch.setattr("pythinker.channels.whatsapp.subprocess.run", fake_run)
    ch = WhatsAppChannel({"enabled": True}, MagicMock())

    assert await ch.login() is True
    assert len(calls) == 1

    _, kwargs = calls[0]
    assert kwargs["cwd"] == bridge_dir
    assert kwargs["env"]["AUTH_DIR"] == str(token_path.parent)
    assert kwargs["env"]["BRIDGE_TOKEN"] == token_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_start_sends_auth_message_with_generated_token(monkeypatch, tmp_path):
    token_path = tmp_path / "whatsapp-auth" / "bridge-token"
    sent_messages: list[str] = []

    class FakeWS:
        def __init__(self) -> None:
            self.close = AsyncMock()

        async def send(self, message: str) -> None:
            sent_messages.append(message)
            ch._running = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeConnect:
        def __init__(self, ws):
            self.ws = ws

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("pythinker.channels.whatsapp._bridge_token_path", lambda: token_path)
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(connect=lambda url: FakeConnect(FakeWS())),
    )

    ch = WhatsAppChannel({"enabled": True, "bridgeUrl": "ws://localhost:3001"}, MagicMock())
    await ch.start()

    assert sent_messages == [
        json.dumps({"type": "auth", "token": token_path.read_text(encoding="utf-8")})
    ]


# ---------------------------------------------------------------------------
# Sprint 1-4 enhancements: read receipts, chunking, dm/group policy, backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_sends_read_receipt_and_phone_jid_presence():
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"]}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    typing_starts: list[str] = []
    ch._start_typing = lambda chat_id: typing_starts.append(chat_id)

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "msg1",
            "sender": "LID123@lid.whatsapp.net",
            "pn": "5551234@s.whatsapp.net",
            "content": "hi",
            "timestamp": 1,
        })
    )

    sent_payloads = [json.loads(call.args[0]) for call in ch._ws.send.call_args_list]
    read_payloads = [p for p in sent_payloads if p.get("type") == "read"]
    assert len(read_payloads) == 1
    assert read_payloads[0]["keys"][0]["remoteJid"] == "LID123@lid.whatsapp.net"
    assert read_payloads[0]["keys"][0]["id"] == "msg1"
    assert typing_starts == ["5551234@s.whatsapp.net"]
    assert ch._presence_for_chat["LID123@lid.whatsapp.net"] == "5551234@s.whatsapp.net"


@pytest.mark.asyncio
async def test_read_receipts_disabled_skips_send():
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"], "sendReadReceipts": False}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "msg2",
            "sender": "1@s.whatsapp.net",
            "pn": "",
            "content": "hi",
            "timestamp": 1,
        })
    )

    sent_payloads = [json.loads(call.args[0]) for call in ch._ws.send.call_args_list]
    assert all(p.get("type") != "read" for p in sent_payloads)


@pytest.mark.asyncio
async def test_dm_policy_disabled_drops_direct_messages():
    ch = WhatsAppChannel({"enabled": True, "allowFrom": ["*"], "dmPolicy": "disabled"}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message",
            "id": "x",
            "sender": "1@s.whatsapp.net",
            "pn": "",
            "content": "hi",
            "timestamp": 1,
        })
    )

    ch._handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dm_policy_allowlist_requires_allow_from():
    ch = WhatsAppChannel(
        {"enabled": True, "dmPolicy": "allowlist", "allowFrom": ["+15551234567"]},
        MagicMock(),
    )
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    blocked = json.dumps({
        "type": "message",
        "id": "blk",
        "sender": "9999@s.whatsapp.net",
        "pn": "",
        "content": "hi",
        "timestamp": 1,
    })
    await ch._handle_bridge_message(blocked)
    ch._handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_policy_allowlist_filters_by_group_jid():
    ch = WhatsAppChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "groupPolicy": "allowlist",
            "groupAllowFrom": ["12345@g.us"],
        },
        MagicMock(),
    )
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message", "id": "g1",
            "sender": "12345@g.us", "pn": "user@s.whatsapp.net",
            "content": "hi", "timestamp": 1, "isGroup": True,
        })
    )
    ch._handle_message.assert_awaited_once()

    ch._handle_message.reset_mock()
    await ch._handle_bridge_message(
        json.dumps({
            "type": "message", "id": "g2",
            "sender": "99999@g.us", "pn": "user@s.whatsapp.net",
            "content": "hi", "timestamp": 2, "isGroup": True,
        })
    )
    ch._handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_chunks_long_text_on_newline_boundary():
    ch = _make_channel()
    # Use a small limit so we exercise chunking
    ch.config.text_chunk_limit = 40
    ch.config.chunk_mode = "newline"
    paragraph = "first sentence here.\n\nsecond paragraph that goes on a bit longer to overflow.\n\nthird."

    await ch.send(OutboundMessage(channel="whatsapp", chat_id="1@s.whatsapp.net", content=paragraph))

    payloads = [json.loads(c.args[0]) for c in ch._ws.send.call_args_list if json.loads(c.args[0]).get("type") == "send"]
    assert len(payloads) >= 2
    # No chunk exceeds the limit
    assert all(len(p["text"]) <= 40 for p in payloads)
    # Reassembling roundtrips (whitespace collapse around split is acceptable)
    assert "first sentence" in payloads[0]["text"]
    assert "third." in payloads[-1]["text"]


@pytest.mark.asyncio
async def test_send_clears_typing_only_on_final_chunk():
    ch = _make_channel()
    chat_id = "1@s.whatsapp.net"
    ch.config.text_chunk_limit = 20
    # Pre-seed a typing task so we can observe whether it gets cancelled
    typing_task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[chat_id] = typing_task

    await ch.send(OutboundMessage(channel="whatsapp", chat_id=chat_id, content="a" * 60))

    # All chunks went out
    sends = [json.loads(c.args[0]) for c in ch._ws.send.call_args_list if json.loads(c.args[0]).get("type") == "send"]
    assert len(sends) == 3
    # Typing was cleared exactly once (before final chunk)
    assert typing_task.cancelled()


@pytest.mark.asyncio
async def test_send_skips_media_over_limit(tmp_path):
    ch = _make_channel()
    ch.config.media_max_mb = 1  # 1 MB cap
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
    small = tmp_path / "small.bin"
    small.write_bytes(b"x" * 1024)  # 1 KB

    await ch.send(
        OutboundMessage(channel="whatsapp", chat_id="1@s.whatsapp.net", content="", media=[str(big), str(small)])
    )

    media_payloads = [
        json.loads(c.args[0]) for c in ch._ws.send.call_args_list
        if json.loads(c.args[0]).get("type") == "send_media"
    ]
    paths_sent = [p["filePath"] for p in media_payloads]
    assert str(small) in paths_sent
    assert str(big) not in paths_sent


@pytest.mark.asyncio
async def test_typing_mode_never_short_circuits_start():
    ch = _make_channel()
    ch.config.typing_mode = "never"

    ch._start_typing("1@s.whatsapp.net")
    assert ch._typing_tasks == {}


@pytest.mark.asyncio
async def test_cancel_typing_resolves_lid_to_phone_target():
    ch = _make_channel()
    lid = "LID@lid.whatsapp.net"
    phone = "5551234@s.whatsapp.net"
    ch._presence_for_chat[lid] = phone
    task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[phone] = task

    await ch._cancel_typing(lid)

    assert task.cancelled()
    # The paused presence packet was sent against the phone JID, not the LID
    paused = [
        json.loads(c.args[0]) for c in ch._ws.send.call_args_list
        if json.loads(c.args[0]).get("state") == "paused"
    ]
    assert paused and paused[0]["to"] == phone


@pytest.mark.asyncio
async def test_cancel_typing_enforces_min_visible_duration():
    """Fast LLM replies must hold the typing indicator long enough to render."""
    import time

    ch = _make_channel()
    ch.config.typing_min_visible_ms = 200
    chat_id = "1@s.whatsapp.net"
    # Simulate a typing task that just started
    typing_task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[chat_id] = typing_task
    ch._typing_started_at[chat_id] = time.monotonic()

    t0 = time.monotonic()
    await ch._cancel_typing(chat_id)
    elapsed_ms = (time.monotonic() - t0) * 1000

    # Should have slept ~200ms (allow some scheduling slop)
    assert elapsed_ms >= 180, f"cancel returned in {elapsed_ms:.0f}ms, expected ≥180ms"
    assert typing_task.cancelled()


@pytest.mark.asyncio
async def test_cancel_typing_skips_wait_when_already_visible():
    """If typing has already been visible long enough, cancel should not sleep."""
    import time

    ch = _make_channel()
    ch.config.typing_min_visible_ms = 200
    chat_id = "1@s.whatsapp.net"
    typing_task = asyncio.create_task(asyncio.sleep(60))
    ch._typing_tasks[chat_id] = typing_task
    # Pretend typing started 1 second ago — already past the threshold
    ch._typing_started_at[chat_id] = time.monotonic() - 1.0

    t0 = time.monotonic()
    await ch._cancel_typing(chat_id)
    elapsed_ms = (time.monotonic() - t0) * 1000

    # Should return promptly, no extra sleep
    assert elapsed_ms < 100, f"cancel waited {elapsed_ms:.0f}ms unnecessarily"
    assert typing_task.cancelled()


def test_reconnect_delay_capped_exponential():
    ch = WhatsAppChannel({"enabled": True, "reconnectJitter": 0}, MagicMock())
    # initial 2s, factor 1.4, max 120s
    d0 = ch._reconnect_delay_seconds(0)
    d1 = ch._reconnect_delay_seconds(1)
    d_huge = ch._reconnect_delay_seconds(50)
    assert abs(d0 - 2.0) < 0.01
    assert abs(d1 - 2.8) < 0.01
    assert d_huge <= 120.0


# ---------------------------------------------------------------------------
# dm_policy = "pairing" — one-time code flow
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_pairings(monkeypatch, tmp_path):
    """Redirect pairings.json to a tmp file so tests don't touch real state."""
    path = tmp_path / "pairings.json"
    monkeypatch.setattr("pythinker.channels.whatsapp._pairings_path", lambda: path)
    yield path


def test_issue_and_consume_pairing_code_happy_path(isolated_pairings):
    info = WhatsAppChannel.issue_pairing_code(ttl_seconds=600)
    assert info["code"].isdigit() and len(info["code"]) == 6

    ch = WhatsAppChannel({"enabled": True}, MagicMock())
    assert ch._consume_pairing_code(info["code"], "1234@s.whatsapp.net") is True
    # Approved list now contains the sender
    assert WhatsAppChannel._is_paired("1234@s.whatsapp.net")
    # Code is one-use — second redemption fails
    assert ch._consume_pairing_code(info["code"], "5678@s.whatsapp.net") is False


def test_consume_pairing_code_rejects_wrong_code(isolated_pairings):
    WhatsAppChannel.issue_pairing_code(ttl_seconds=600)
    ch = WhatsAppChannel({"enabled": True}, MagicMock())
    assert ch._consume_pairing_code("999999", "x@s.whatsapp.net") is False


def test_consume_pairing_code_rejects_expired(isolated_pairings, monkeypatch):
    info = WhatsAppChannel.issue_pairing_code(ttl_seconds=60)
    # Fast-forward "now" past the expiry
    import pythinker.channels.whatsapp as wa
    monkeypatch.setattr(wa, "_now_ts", lambda: info["expires_at"] + 1)

    ch = WhatsAppChannel({"enabled": True}, MagicMock())
    assert ch._consume_pairing_code(info["code"], "y@s.whatsapp.net") is False


@pytest.mark.asyncio
async def test_pairing_mode_unknown_sender_with_valid_code_promotes(isolated_pairings):
    info = WhatsAppChannel.issue_pairing_code(ttl_seconds=600)
    ch = WhatsAppChannel({"enabled": True, "dmPolicy": "pairing"}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message", "id": "p1",
            "sender": "9999@s.whatsapp.net", "pn": "",
            "content": f"/pair {info['code']}", "timestamp": 1,
        })
    )

    # The /pair message itself is not forwarded to the agent
    ch._handle_message.assert_not_awaited()
    # Bot sent a confirmation reply
    sent = [json.loads(c.args[0]) for c in ch._ws.send.call_args_list]
    assert any(p.get("type") == "send" and "Paired" in p.get("text", "") for p in sent)
    # Sender is now in approved list, and subsequent messages flow through
    await ch._handle_bridge_message(
        json.dumps({
            "type": "message", "id": "p2",
            "sender": "9999@s.whatsapp.net", "pn": "",
            "content": "hello", "timestamp": 2,
        })
    )
    ch._handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_pairing_mode_unknown_sender_gets_hint_once(isolated_pairings):
    ch = WhatsAppChannel({"enabled": True, "dmPolicy": "pairing"}, MagicMock())
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    payload = json.dumps({
        "type": "message", "id": "h1",
        "sender": "abc@s.whatsapp.net", "pn": "",
        "content": "hi", "timestamp": 1,
    })
    await ch._handle_bridge_message(payload)
    # Second message uses a different id so dedup doesn't suppress
    payload2 = json.dumps({
        "type": "message", "id": "h2",
        "sender": "abc@s.whatsapp.net", "pn": "",
        "content": "anyone there?", "timestamp": 2,
    })
    await ch._handle_bridge_message(payload2)

    ch._handle_message.assert_not_awaited()
    hints = [
        json.loads(c.args[0]) for c in ch._ws.send.call_args_list
        if json.loads(c.args[0]).get("type") == "send"
        and "pairing" in json.loads(c.args[0]).get("text", "").lower()
    ]
    assert len(hints) == 1  # exactly one hint, not one per message


@pytest.mark.asyncio
async def test_pairing_mode_allow_from_bypasses_pairing(isolated_pairings):
    # sender_id is the prefix before @, matching how allow_from entries are stored
    ch = WhatsAppChannel(
        {"enabled": True, "dmPolicy": "pairing", "allowFrom": ["trusted"]},
        MagicMock(),
    )
    ch._handle_message = AsyncMock()
    ch._ws = AsyncMock()
    ch._connected = True

    await ch._handle_bridge_message(
        json.dumps({
            "type": "message", "id": "t1",
            "sender": "trusted@s.whatsapp.net", "pn": "",
            "content": "hi", "timestamp": 1,
        })
    )
    ch._handle_message.assert_awaited_once()


def test_parse_pair_command_variants():
    assert WhatsAppChannel._parse_pair_command("/pair 123456") == "123456"
    assert WhatsAppChannel._parse_pair_command("  /PAIR 999  ") == "999"
    assert WhatsAppChannel._parse_pair_command("/pair") == ""
    assert WhatsAppChannel._parse_pair_command("/pair abc") == ""
    assert WhatsAppChannel._parse_pair_command("/pairing 1234") is None
    assert WhatsAppChannel._parse_pair_command("hello") is None
