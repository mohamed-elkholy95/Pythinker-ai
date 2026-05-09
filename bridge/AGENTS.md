# AGENTS.md

Scoped rules for `bridge/`. Root [`../AGENTS.md`](../AGENTS.md) applies first. Keep this file bridge-specific only.

## Scope

Node.js WhatsApp relay for Pythinker. Thin transport over [Baileys](https://github.com/WhiskeySockets/Baileys). Python owns routing, policy, persistence beyond Baileys creds, and all business logic.

The Python side spawns this process and talks to it over localhost WebSocket. `bridge/src/` ships in the wheel as `pythinker/bridge/` via root `pyproject.toml`.

## Commands

```bash
npm install            # install deps (Node >= 20)
npm run build          # tsc -> dist/
npm run dev            # build + start; requires BRIDGE_TOKEN
npm start              # run dist/index.js
npx tsc --noEmit       # type-check only
```

No bridge test suite or linter currently exists.

Standalone env vars:

- `BRIDGE_TOKEN` required; shared secret for WS auth handshake. Pythinker provisions it when spawning the bridge.
- `BRIDGE_PORT` default `3001`.
- `AUTH_DIR` default `~/.pythinker/whatsapp-auth`; Baileys auth state lives here, sibling `media/` receives downloads.

## Architecture

Dependency direction: `src/index.ts` -> `src/server.ts` -> `src/whatsapp.ts`.

- `index.ts`: entrypoint. Polyfills `globalThis.crypto`, validates `BRIDGE_TOKEN`, wires SIGINT/SIGTERM, starts `BridgeServer`.
- `server.ts`: localhost-only `ws` server. Rejects browser `Origin`; first client message must be `{type:"auth", token}` within 5 s or the socket closes.
- `whatsapp.ts`: Baileys wrapper. Persists multi-file auth state, forwards QR/status/message/error events to Python, downloads media into `<authDir>/../media/`, detects group mentions against bot jid/lid.

## Wire protocol

Private Python <-> bridge contract. If changing, update `pythinker/channels/whatsapp.py` in the same change.

Client -> server after auth:

- `{type:"send", to, text}`
- `{type:"send_media", to, filePath, mimetype, caption?, fileName?}`

Server -> client:

- `{type:"qr", qr}`
- `{type:"status", status}` with `"connected"` or `"disconnected"`
- `{type:"message", id, sender, pn, content, timestamp, isGroup, wasMentioned?, media?}`
- `{type:"sent", to}` or `{type:"error", error}`

## Footguns

- Baileys is pinned to `7.0.0-rc.9` and moves fast; existing `any` casts in `whatsapp.ts` are intentional.
- Mention detection compares normalized jid, lid, and jid with `:device` stripped. Keep all three surfaces.
- Media pruning belongs to Python, not the bridge.
- Do not add auth policy, routing, rate limits, or feature logic here; put it in Python.
