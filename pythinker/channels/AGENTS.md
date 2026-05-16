# AGENTS.md — `pythinker/channels/`

Scoped rules for chat-platform adapters. Root [`../../AGENTS.md`](../../AGENTS.md) applies first.

## Scope

Adapters that connect Pythinker to chat platforms (Telegram, Slack, Discord, WhatsApp, Matrix, MS Teams, Email, WebSocket). The `ChannelManager` starts/stops adapters and dispatches outbound messages from the bus.

## Rules

- Keep chat-platform quirks in the adapter: Telegram HTML parsing, Slack threading/mrkdwn, MS Teams JWT validation, WhatsApp bridge integration, etc. Do not leak quirks into the agent loop or bus.
- Adding a channel requires:
  1. `pythinker/channels/<name>.py`.
  2. Registry entry in `registry.py`.
  3. `ChannelsConfig` schema update in `pythinker/config/schema.py`.
  4. Docs in `docs/chat-apps.md` and/or `docs/channel-plugin-guide.md`.
  5. Tests under `tests/channels/`.
- Third-party channels discover through the `pythinker.channels` entry point.
- Use the words "channel/channels" or "chat platform" in docs/UI/changelog; `pythinker/channels/` is the internal layout name.
- WhatsApp adapter pairs with the Node bridge — see [`../../bridge/AGENTS.md`](../../bridge/AGENTS.md). Any wire-protocol change must update `whatsapp.py` and `bridge/src/` in the same commit.
- WebSocket adapter pairs with the WebUI — see [`../../webui/AGENTS.md`](../../webui/AGENTS.md). Wire envelope changes must update `webui/src/lib/types.ts` in the same commit.

## Verification

```bash
uv run pytest tests/channels/
uv run pytest tests/channels/test_telegram_channel.py
uv run ruff check pythinker/channels --select F401,F841
```
