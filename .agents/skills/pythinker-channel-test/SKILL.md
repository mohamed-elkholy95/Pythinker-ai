---
name: pythinker-channel-test
description: End-to-end test a new or modified Pythinker channel adapter (Telegram, Slack, Discord, Matrix, WhatsApp, MS Teams, WebSocket, Email).
metadata:
  pythinker:
    emoji: "📡"
    requires:
      bins: ["uv"]
---

# Pythinker Channel Test Companion

Use when adding, modifying, or debugging a channel adapter under
`pythinker/channels/`.

## Channel Layout

Each channel lives in `pythinker/channels/`:

- `<name>.py` — the adapter (subclasses `BaseChannel` from `base.py`)
- `base.py` — abstract base; **abstract methods are
  `start`, `stop`, `send(OutboundMessage)`** (`pythinker/channels/base.py:78-96`)
- `manager.py` — startup/shutdown + outbound dispatch with stream-delta
  coalescing and 1 s / 2 s / 4 s retries
- `registry.py` — config-name → class map; supports the
  `pythinker.channels` entry point for third-party plugins

A new channel touches **5 places**:

1. `pythinker/channels/<name>.py` — adapter
2. `pythinker/channels/registry.py` — registration
3. `pythinker/config/schema.py` — `ChannelsConfig` field
4. `docs/chat-apps.md` and/or `docs/channel-plugin-guide.md` — doc page
5. `tests/channels/test_<name>.py` — tests

## Adapter Skeleton

```python
# pythinker/channels/mychannel.py
from pythinker.bus.events import OutboundMessage
from pythinker.channels.base import BaseChannel


class MyChannelAdapter(BaseChannel):
    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def send(self, msg: OutboundMessage) -> None:
        # Translate the OutboundMessage envelope into a vendor call.
        ...

    # Optional: override send_delta(...) for streaming-aware channels.
```

`BaseChannel` provides `send_delta`, `transcribe_audio`, `login`, and
the `_handle_message` ingress helper. Override only what your channel
needs.

## Per-Channel Quirks To Test

| Channel | File | Quirk |
|---------|------|-------|
| Telegram | `telegram.py` (~1203 LOC) | `parse_mode` HTML pipeline breaks on nested tags |
| Slack | `slack.py` | `mrkdwn` fixup strips trailing `\n`; verify `thread_ts` propagation |
| Discord | `discord.py` | Webhook delivery; rate limit via `retry_after` on 429 |
| Matrix | `matrix.py` | Needs `libolm-dev`; missing → startup crypto errors. Room ID `!roomid:matrix.org` |
| WhatsApp | `whatsapp.py` + `bridge/` | Baileys connection state machine; bridge is a thin Node relay (force-included into the wheel as `pythinker/bridge/`) |
| MS Teams | `msteams.py` | JWT validation in `validate_jwt`; check token expiry |
| WebSocket | `websocket.py` (~1637 LOC) | Signed media URL secret regenerates on restart — old links 401 by design. Image limits: `_MAX_IMAGES_PER_MESSAGE=4`, `_MAX_IMAGE_BYTES=8 MB`, MIME `{png,jpeg,webp,gif}` |
| Email | `email.py` | SMTP vs IMAP creds in `~/.pythinker/credentials/`; MIME whitelist |

## Outbound Dispatch

`ChannelManager._dispatch_outbound` is the only consumer of
`MessageBus.outbound`. It:

- Coalesces stream deltas before flushing
- Retries on transient send errors with 1 s / 2 s / 4 s backoff
- Surfaces terminal failures back into the bus as system messages

If your channel has its own retry policy, decide whether it shadows
the manager's — usually you want the manager to own retries.

## Test Execution

```bash
# Full channel suite
uv run pytest tests/channels/ -v

# One channel
uv run pytest tests/channels/test_telegram.py -v

# Cross-cutting (channel + provider)
uv run pytest tests/channels/ tests/providers/

# Lint gate (CI strictest)
uv run ruff check pythinker --select F401,F841
```

## Boundaries

- Platform quirks live in the channel adapter — core stays generic
- If a bug names a channel, start in that module; add a generic core
  seam only when multiple channels need it
- Third-party plugins discover via the `pythinker.channels`
  entry point — don't bake plugin ids into core
- The Node bridge is a thin relay; ALL business logic stays in Python
