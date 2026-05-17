"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
import mimetypes
import os
import random
import secrets
import shutil
import subprocess
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import Field

from pythinker.bus.events import OutboundMessage
from pythinker.bus.queue import MessageBus
from pythinker.channels.base import BaseChannel
from pythinker.config.schema import Base

CONFIG_FIELDS = {
    "label": "WhatsApp",
    "required_secrets": ["channels.whatsapp.bridge_token"],
    "fields": [
        "enabled",
        "bridge_url",
        "bridge_token",
        "allow_from",
        "group_policy",
        "group_allow_from",
        "dm_policy",
        "send_read_receipts",
        "typing_mode",
        "typing_interval_seconds",
        "typing_min_visible_ms",
        "text_chunk_limit",
        "chunk_mode",
        "media_max_mb",
        "reconnect_initial_ms",
        "reconnect_max_ms",
        "reconnect_factor",
        "reconnect_jitter",
    ],
    "local_dependency_checks": [],
}

# Fallback typing refresh when config is missing; 6s stays comfortably under
# Baileys' ~10s presence expiry.
PRESENCE_REFRESH_SECONDS = 6
PresenceState = Literal["available", "unavailable", "composing", "recording", "paused"]


class WhatsAppConfig(Base):
    """WhatsApp channel configuration."""

    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    # "open" responds to all messages in any allowed group, "mention" only when
    # the bot is @mentioned, "allowlist" gates which group JIDs are listened to.
    group_policy: Literal["open", "mention", "allowlist"] = "open"
    group_allow_from: list[str] = Field(default_factory=list)
    # "open" responds to any DM passing allow_from, "allowlist" requires
    # explicit allow_from membership, "disabled" silently ignores all DMs,
    # "pairing" requires the sender to redeem a one-time code via /pair.
    dm_policy: Literal["open", "allowlist", "disabled", "pairing"] = "open"
    pairing_code_ttl_seconds: int = 600
    send_read_receipts: bool = True
    typing_mode: Literal["thinking", "never"] = "thinking"
    typing_interval_seconds: int = 6
    # WhatsApp Web needs ~500-1000 ms of sustained `composing` before the
    # client animates "typing…". For fast LLM replies, hold the indicator
    # this long after starting before allowing it to be cleared.
    typing_min_visible_ms: int = 800
    text_chunk_limit: int = 4000
    chunk_mode: Literal["length", "newline"] = "newline"
    media_max_mb: int = 50
    reconnect_initial_ms: int = 2000
    reconnect_max_ms: int = 120000
    reconnect_factor: float = 1.4
    reconnect_jitter: float = 0.2


def _bridge_token_path() -> Path:
    from pythinker.config.paths import get_runtime_subdir

    return get_runtime_subdir("whatsapp-auth") / "bridge-token"


def _pairings_path() -> Path:
    from pythinker.config.paths import get_runtime_subdir

    return get_runtime_subdir("whatsapp-auth") / "pairings.json"


def _load_pairings() -> dict[str, Any]:
    path = _pairings_path()
    if not path.exists():
        return {"pending": {}, "approved": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pending": {}, "approved": []}
    data.setdefault("pending", {})
    data.setdefault("approved", [])
    return data


def _save_pairings(data: dict[str, Any]) -> None:
    path = _pairings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _now_ts() -> int:
    import time

    return int(time.time())


def _prune_expired_pairings(data: dict[str, Any]) -> bool:
    """Drop expired pending codes. Returns True if anything was removed."""
    now = _now_ts()
    pending = data.get("pending", {})
    expired = [code for code, meta in pending.items() if int(meta.get("expires_at", 0)) <= now]
    for code in expired:
        pending.pop(code, None)
    return bool(expired)


def _load_or_create_bridge_token(path: Path) -> str:
    """Load a persisted bridge token or create one on first use."""
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"
    display_name = "WhatsApp"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WhatsAppConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WhatsAppConfig.model_validate(config)
        super().__init__(config, bus)
        self._ws = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._lid_to_phone: dict[str, str] = {}
        self._typing_tasks: dict[str, asyncio.Task[None]] = {}
        # Map chat_id (the JID we reply to, often a LID) → presence_target
        # (the @s.whatsapp.net JID we send chat-level composing to). WhatsApp
        # silently drops presence sent to LID JIDs, so the two can differ.
        self._presence_for_chat: dict[str, str] = {}
        # monotonic timestamp per presence_target — used to enforce a minimum
        # visible duration so fast LLM replies don't blink the indicator.
        self._typing_started_at: dict[str, float] = {}
        # Senders we've already nudged with a pairing hint, to avoid bouncing
        # a help message back on every keystroke from an unknown contact.
        self._pairing_hinted: set[str] = set()
        self._bridge_token: str | None = None

    def _effective_bridge_token(self) -> str:
        """Resolve the bridge token, generating a local secret when needed."""
        if self._bridge_token is not None:
            return self._bridge_token
        configured = self.config.bridge_token.strip()
        if configured:
            self._bridge_token = configured
        else:
            self._bridge_token = _load_or_create_bridge_token(_bridge_token_path())
        return self._bridge_token

    async def login(self, force: bool = False) -> bool:
        """
        Set up and run the WhatsApp bridge for login.

        This spawns the Node.js bridge process which handles the WhatsApp
        authentication flow. The process blocks until the user scans the QR code,
        enters the pairing code, or interrupts with Ctrl+C.
        """
        try:
            bridge_dir = _ensure_bridge_setup()
        except RuntimeError as e:
            logger.error("{}", e)
            return False

        env = {**os.environ}
        env["BRIDGE_TOKEN"] = self._effective_bridge_token()
        env["AUTH_DIR"] = str(_bridge_token_path().parent)

        logger.info("Starting WhatsApp bridge for login...")
        try:
            subprocess.run(
                [shutil.which("npm"), "start"], cwd=bridge_dir, check=True, env=env
            )
        except subprocess.CalledProcessError:
            return False

        return True

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url

        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)

        self._running = True
        attempt = 0

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    await ws.send(
                        json.dumps({"type": "auth", "token": self._effective_bridge_token()})
                    )
                    self._connected = True
                    attempt = 0  # reset backoff on successful connect
                    logger.info("Connected to WhatsApp bridge")

                    # Listen for messages
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: {}", e)

                self._connected = False
                self._ws = None
                await self._cancel_all_typing_tasks()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                await self._cancel_all_typing_tasks()
                logger.warning("WhatsApp bridge connection error: {}", e)

                if self._running:
                    delay = self._reconnect_delay_seconds(attempt)
                    attempt += 1
                    logger.info("Reconnecting in {:.1f}s (attempt {})", delay, attempt)
                    await asyncio.sleep(delay)

    def _reconnect_delay_seconds(self, attempt: int) -> float:
        """Capped exponential backoff with symmetric jitter."""
        cfg = self.config
        base = (cfg.reconnect_initial_ms / 1000.0) * (cfg.reconnect_factor ** attempt)
        capped = min(base, cfg.reconnect_max_ms / 1000.0)
        jitter = cfg.reconnect_jitter
        if jitter:
            capped *= 1 + random.uniform(-jitter, jitter)
        return max(0.1, capped)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        await self._cancel_all_typing_tasks()
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def set_presence(self, chat_id: str, state: PresenceState) -> None:
        """Send a best-effort WhatsApp presence update through the bridge."""
        if not self._ws or not self._connected:
            return

        try:
            payload = {"type": "presence", "to": chat_id, "state": state}
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.debug("Error sending WhatsApp presence {} to {}: {}", state, chat_id, e)

    async def _typing_loop(self, chat_id: str) -> None:
        interval = max(1, int(getattr(self.config, "typing_interval_seconds", PRESENCE_REFRESH_SECONDS)))
        try:
            await self.set_presence(chat_id, "available")
            while True:
                await self.set_presence(chat_id, "composing")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("WhatsApp typing loop stopped for {}: {}", chat_id, e)

    def _start_typing(self, chat_id: str) -> None:
        if not chat_id or not self._ws or not self._connected:
            return
        if getattr(self.config, "typing_mode", "thinking") == "never":
            return

        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

        import time as _time
        self._typing_started_at[chat_id] = _time.monotonic()
        new_task = asyncio.create_task(self._typing_loop(chat_id))
        self._typing_tasks[chat_id] = new_task

        def _discard(done: asyncio.Task[None], key: str = chat_id) -> None:
            if self._typing_tasks.get(key) is done:
                self._typing_tasks.pop(key, None)

        new_task.add_done_callback(_discard)

    async def _cancel_typing(self, chat_id: str, *, send_paused: bool = True) -> None:
        # Resolve the typing task by the presence target we actually used (may
        # differ from chat_id when the inbound message came from a LID).
        presence_target = self._presence_for_chat.pop(chat_id, chat_id)
        started_at = self._typing_started_at.pop(presence_target, None)
        task = self._typing_tasks.pop(presence_target, None)
        if task:
            # Enforce the minimum visible duration BEFORE cancelling so the
            # loop keeps refreshing `composing` while we wait. A fast LLM
            # reply that would otherwise blink the indicator instead holds it
            # visible long enough for the WhatsApp client to render.
            if started_at is not None:
                import time as _time
                min_ms = max(0, int(getattr(self.config, "typing_min_visible_ms", 800)))
                if min_ms:
                    elapsed_ms = (_time.monotonic() - started_at) * 1000
                    remaining = (min_ms - elapsed_ms) / 1000
                    if remaining > 0:
                        await asyncio.sleep(remaining)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        if task and send_paused:
            await self.set_presence(presence_target, "paused")

    async def _cancel_all_typing_tasks(self) -> None:
        tasks = list(self._typing_tasks.values())
        self._typing_tasks.clear()
        self._presence_for_chat.clear()
        self._typing_started_at.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _should_clear_typing(msg: OutboundMessage) -> bool:
        if not (msg.content or msg.media):
            return False
        metadata = msg.metadata or {}
        return not (
            metadata.get("_progress")
            or metadata.get("_tool_hint")
            or metadata.get("_stream_delta")
            or metadata.get("_stream_end")
        )

    async def _send_pairing_reply(self, chat_id: str, text: str) -> None:
        """Send a short administrative reply over the bridge (best-effort)."""
        if not self._ws or not self._connected or not chat_id:
            return
        try:
            payload = {"type": "send", "to": chat_id, "text": text}
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.debug("Error sending WhatsApp pairing reply to {}: {}", chat_id, e)

    async def _send_read_receipt(self, remote_jid: str, message_id: str) -> None:
        """Mark an inbound WhatsApp message as read (blue ticks)."""
        if not self._ws or not self._connected:
            return
        if not getattr(self.config, "send_read_receipts", True):
            return
        if not remote_jid or not message_id:
            return
        try:
            payload = {
                "type": "read",
                "keys": [{"remoteJid": remote_jid, "id": message_id, "fromMe": False}],
            }
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.debug("Error sending WhatsApp read receipt to {}: {}", remote_jid, e)

    def _chunk_text(self, text: str) -> list[str]:
        """Split outbound text on the configured chunk limit; soft-prefers newline boundaries."""
        limit = max(1, int(getattr(self.config, "text_chunk_limit", 4000)))
        if len(text) <= limit:
            return [text]

        mode = getattr(self.config, "chunk_mode", "newline")
        chunks: list[str] = []
        remaining = text
        while len(remaining) > limit:
            window = remaining[:limit]
            split_at = -1
            if mode == "newline":
                # Prefer a paragraph break, then a single newline, then a space —
                # only count splits in the back half of the window so we don't
                # ship absurdly short chunks.
                for marker in ("\n\n", "\n", " "):
                    idx = window.rfind(marker)
                    if idx >= limit // 2:
                        split_at = idx + len(marker)
                        break
            if split_at <= 0:
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _dm_policy_allows(self, sender_id: str) -> bool:
        """Apply dmPolicy to a non-group sender, layered on top of allow_from."""
        policy = getattr(self.config, "dm_policy", "open")
        if policy == "disabled":
            return False
        if policy == "allowlist":
            allow = getattr(self.config, "allow_from", []) or []
            return sender_id in allow or "*" in allow
        if policy == "pairing":
            allow = getattr(self.config, "allow_from", []) or []
            if sender_id in allow or "*" in allow:
                return True
            return self._is_paired(sender_id)
        return True

    @staticmethod
    def _is_paired(sender_id: str) -> bool:
        if not sender_id:
            return False
        return sender_id in _load_pairings().get("approved", [])

    def is_allowed(self, sender_id: str) -> bool:
        """Allow senders that are either in config.allow_from or have paired."""
        if super().is_allowed(sender_id):
            return True
        return self._is_paired(sender_id)

    @staticmethod
    def issue_pairing_code(ttl_seconds: int = 600, label: str | None = None) -> dict[str, Any]:
        """Generate and persist a new pairing code. Returns {code, expires_at}.

        Caller (CLI or admin tool) is responsible for surfacing the code to the
        user who should redeem it. Codes are 6 digits, easy to read aloud.
        """
        ttl = max(60, int(ttl_seconds))
        data = _load_pairings()
        _prune_expired_pairings(data)
        # Avoid collisions with existing pending codes.
        code = ""
        for _ in range(20):
            candidate = f"{secrets.randbelow(1_000_000):06d}"
            if candidate not in data["pending"]:
                code = candidate
                break
        if not code:
            raise RuntimeError("Failed to generate a unique pairing code; try again")
        now = _now_ts()
        data["pending"][code] = {
            "issued_at": now,
            "expires_at": now + ttl,
            "label": label,
        }
        _save_pairings(data)
        return {"code": code, "expires_at": now + ttl, "ttl_seconds": ttl}

    def _consume_pairing_code(self, code: str, sender_id: str) -> bool:
        """Redeem a pairing code for a sender. Returns True on success."""
        if not code or not sender_id:
            return False
        data = _load_pairings()
        _prune_expired_pairings(data)
        meta = data.get("pending", {}).get(code)
        if not meta:
            _save_pairings(data)  # persist pruning even on failure
            return False
        data["pending"].pop(code, None)
        approved = data.setdefault("approved", [])
        if sender_id not in approved:
            approved.append(sender_id)
        _save_pairings(data)
        return True

    @staticmethod
    def _parse_pair_command(content: str) -> str | None:
        """Extract the code from `/pair <code>` (case-insensitive). Returns None on no match.

        Returns the digit code on success, "" if the message is exactly /pair
        with no/bad code, and None when the message isn't a pair command at
        all (so callers can distinguish "ignore" from "bad attempt").
        """
        if not content:
            return None
        stripped = content.strip()
        lower = stripped.lower()
        if lower != "/pair" and not lower.startswith("/pair "):
            return None
        rest = stripped[len("/pair") :].strip()
        if rest and rest.isdigit():
            return rest
        return ""  # /pair with no/bad code

    def _group_policy_allows(self, group_jid: str, was_mentioned: bool) -> bool:
        """Apply groupPolicy + groupAllowFrom to an inbound group message."""
        policy = getattr(self.config, "group_policy", "open")
        if policy == "mention":
            return bool(was_mentioned)
        if policy == "allowlist":
            allow = getattr(self.config, "group_allow_from", []) or []
            return group_jid in allow or "*" in allow
        return True

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        chat_id = msg.chat_id
        clear_typing = self._should_clear_typing(msg)

        if msg.content:
            chunks = self._chunk_text(msg.content)
            for index, chunk in enumerate(chunks):
                # Only the final chunk clears typing; intermediate chunks let
                # the indicator stay visible while the rest streams in.
                is_last = index == len(chunks) - 1
                if clear_typing and is_last:
                    await self._cancel_typing(chat_id)
                try:
                    payload = {"type": "send", "to": chat_id, "text": chunk}
                    await self._ws.send(json.dumps(payload, ensure_ascii=False))
                except Exception as e:
                    logger.error("Error sending WhatsApp message: {}", e)
                    raise
        elif clear_typing and msg.media:
            # Media-only message: drop typing right before the upload.
            await self._cancel_typing(chat_id)

        media_max_bytes = max(0, int(getattr(self.config, "media_max_mb", 50))) * 1024 * 1024
        for media_path in msg.media or []:
            try:
                if media_max_bytes:
                    try:
                        size = os.path.getsize(media_path)
                    except OSError:
                        size = 0
                    if size and size > media_max_bytes:
                        logger.warning(
                            "Skipping WhatsApp media {}: {} bytes exceeds limit {} bytes",
                            media_path,
                            size,
                            media_max_bytes,
                        )
                        continue
                mime, _ = mimetypes.guess_type(media_path)
                payload = {
                    "type": "send_media",
                    "to": chat_id,
                    "filePath": media_path,
                    "mimetype": mime or "application/octet-stream",
                    "fileName": media_path.rsplit("/", 1)[-1],
                }
                await self._ws.send(json.dumps(payload, ensure_ascii=False))
            except Exception as e:
                logger.error("Error sending WhatsApp media {}: {}", media_path, e)
                raise

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: {}", raw[:100])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            # Incoming message from WhatsApp
            # Deprecated by whatsapp: old phone number style typically: <phone>@s.whatspp.net
            pn = data.get("pn", "")
            # New LID sytle typically:
            sender = data.get("sender", "")
            content = data.get("content", "")
            message_id = data.get("id", "")

            # Extract just the phone number or lid as chat_id
            is_group = data.get("isGroup", False)
            was_mentioned = data.get("wasMentioned", False)

            if is_group:
                if not self._group_policy_allows(sender, was_mentioned):
                    return

            # Classify by JID suffix: @s.whatsapp.net = phone, @lid.whatsapp.net = LID
            # The bridge's pn/sender fields don't consistently map to phone/LID across versions.
            raw_a = pn or ""
            raw_b = sender or ""
            id_a = raw_a.split("@")[0] if "@" in raw_a else raw_a
            id_b = raw_b.split("@")[0] if "@" in raw_b else raw_b

            phone_id = ""
            lid_id = ""
            for raw, extracted in [(raw_a, id_a), (raw_b, id_b)]:
                if "@s.whatsapp.net" in raw:
                    phone_id = extracted
                elif "@lid.whatsapp.net" in raw:
                    lid_id = extracted
                elif extracted and not phone_id:
                    phone_id = extracted  # best guess for bare values

            if phone_id and lid_id:
                self._lid_to_phone[lid_id] = phone_id
            sender_id = phone_id or self._lid_to_phone.get(lid_id, "") or lid_id or id_a or id_b

            # Pairing flow: if dm_policy=="pairing" and sender is unknown,
            # try to redeem a /pair <code>. Successful redemption silently
            # promotes the sender to the approved list and falls through to
            # normal processing of any remaining message body. A missing or
            # wrong code triggers a one-shot hint and then drops the message.
            if (
                not is_group
                and getattr(self.config, "dm_policy", "open") == "pairing"
                and not self._dm_policy_allows(sender_id)
            ):
                pair_code = self._parse_pair_command(content)
                redeemed = False
                if pair_code:
                    redeemed = self._consume_pairing_code(pair_code, sender_id)
                if redeemed:
                    await self._send_pairing_reply(
                        sender,
                        "✅ Paired. You can now message this assistant.",
                    )
                    self._pairing_hinted.discard(sender_id)
                    return
                # Not paired yet: emit a hint once per sender, then ignore.
                if sender_id not in self._pairing_hinted:
                    self._pairing_hinted.add(sender_id)
                    await self._send_pairing_reply(
                        sender,
                        "👋 This assistant requires pairing. Ask the owner for a code, "
                        "then reply with `/pair <code>` (6 digits).",
                    )
                return

            if not is_group and not self._dm_policy_allows(sender_id):
                return

            if not self.is_allowed(sender_id):
                logger.warning(
                    "Access denied for sender {} on channel {}. "
                    "Add them to allowFrom list in config to grant access.",
                    sender_id,
                    self.name,
                )
                return

            if message_id:
                if message_id in self._processed_message_ids:
                    return
                self._processed_message_ids[message_id] = None
                while len(self._processed_message_ids) > 1000:
                    self._processed_message_ids.popitem(last=False)

            logger.info("Sender phone={} lid={} → sender_id={}", phone_id or "(empty)", lid_id or "(empty)", sender_id)
            # Fire-and-forget read receipt; uses the original remote JID so
            # WhatsApp routes the ack to the right chat (group or DM).
            await self._send_read_receipt(sender, message_id)
            # Use the phone JID for typing when known — chat-level presence to
            # @lid.whatsapp.net is often silently dropped by the WhatsApp client.
            presence_target = (
                f"{phone_id}@s.whatsapp.net" if phone_id and not is_group else sender
            )
            if sender:
                self._presence_for_chat[sender] = presence_target
            self._start_typing(presence_target)

            # Extract media paths (images/documents/videos downloaded by the bridge)
            media_paths = data.get("media") or []

            # Handle voice transcription if it's a voice message
            if content == "[Voice Message]":
                if media_paths:
                    logger.info("Transcribing voice message from {}...", sender_id)
                    transcription = await self.transcribe_audio(media_paths[0])
                    if transcription:
                        content = transcription
                        # The .ogg path was only useful for transcription. Drop it so
                        # downstream tagging does not append `[file: ...voice.ogg]`,
                        # which would otherwise reach the LLM and prompt a "cannot
                        # process audio" reply despite successful transcription.
                        media_paths = []
                        logger.info("Transcribed voice from {}: {}...", sender_id, transcription[:50])
                    else:
                        content = "[Voice Message: Transcription failed]"
                else:
                    content = "[Voice Message: Audio not available]"

            # Build content tags matching Telegram's pattern: [image: /path] or [file: /path]
            if media_paths:
                for p in media_paths:
                    mime, _ = mimetypes.guess_type(p)
                    media_type = "image" if mime and mime.startswith("image/") else "file"
                    media_tag = f"[{media_type}: {p}]"
                    content = f"{content}\n{media_tag}" if content else media_tag

            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,  # Use full LID for replies
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False),
                },
            )

        elif msg_type == "status":
            # Connection status update
            status = data.get("status")
            logger.info("WhatsApp status: {}", status)

            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False
                await self._cancel_all_typing_tasks()

        elif msg_type == "qr":
            # QR code for authentication
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")

        elif msg_type == "error":
            logger.error("WhatsApp bridge error: {}", data.get("error"))


def _ensure_bridge_setup() -> Path:
    """
    Ensure the WhatsApp bridge is set up and built.

    Returns the bridge directory. Raises RuntimeError if npm is not found
    or bridge cannot be built.

    PYTHINKER_BRIDGE_SOURCE_DIR overrides the install location for dev
    workflows: when set, the bridge runs directly from that path so
    `npm run build` in the repo takes effect on next restart without a
    re-deploy. The directory must already contain a built `dist/index.js`.
    """
    from pythinker.config.paths import get_bridge_install_dir

    dev_dir = os.environ.get("PYTHINKER_BRIDGE_SOURCE_DIR")
    if dev_dir:
        dev_path = Path(dev_dir).expanduser().resolve()
        if not (dev_path / "package.json").exists():
            raise RuntimeError(
                f"PYTHINKER_BRIDGE_SOURCE_DIR={dev_path} has no package.json"
            )
        if not (dev_path / "dist" / "index.js").exists():
            raise RuntimeError(
                f"PYTHINKER_BRIDGE_SOURCE_DIR={dev_path}: dist/index.js missing; "
                "run `npm install && npm run build` in that directory first"
            )
        logger.info("Using dev WhatsApp bridge from {}", dev_path)
        return dev_path

    user_bridge = get_bridge_install_dir()

    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    npm_path = shutil.which("npm")
    if not npm_path:
        raise RuntimeError("npm not found. Please install Node.js >= 20.")

    # Find source bridge
    current_file = Path(__file__)
    pkg_bridge = current_file.parent.parent / "bridge"
    src_bridge = current_file.parent.parent.parent / "bridge"

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        raise RuntimeError(
            "WhatsApp bridge source not found. "
            "Try reinstalling: pip install --force-reinstall pythinker"
        )

    logger.info("Setting up WhatsApp bridge...")
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    logger.info("  Installing dependencies...")
    subprocess.run([npm_path, "install"], cwd=user_bridge, check=True, capture_output=True)

    logger.info("  Building...")
    subprocess.run([npm_path, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

    logger.info("Bridge ready")
    return user_bridge
