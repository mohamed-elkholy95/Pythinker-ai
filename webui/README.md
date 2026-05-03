# pythinker webui

The browser front-end for the pythinker gateway. It is built with Vite + React 18 +
TypeScript + Tailwind 3 + shadcn/ui, talks to the gateway over the WebSocket
multiplex protocol, and reads session metadata from the embedded REST surface
on the same port.

For the project overview, install guide, and general docs map, see the root
[`README.md`](../README.md).

## Current status

> [!NOTE]
> The standalone WebUI development workflow currently requires a source
> checkout.
>
> WebUI changes in the GitHub repository may land before they are included in
> the next packaged release, so source installs and published package versions
> are not yet guaranteed to move in lockstep.

## Layout

```text
webui/                 source tree (this directory)
pythinker/web/dist/      build output served by the gateway
```

## Develop from source

### 1. Install pythinker from source

From the repository root:

```bash
pip install -e .
```

### 2. Enable the WebSocket channel

In `~/.pythinker/config.json`:

```json
{ "channels": { "websocket": { "enabled": true } } }
```

### 3. Start the gateway

In one terminal:

```bash
pythinker gateway
```

### 4. Start the WebUI dev server

In another terminal:

```bash
cd webui
bun install            # npm install also works
bun run dev
```

Then open `http://127.0.0.1:5173`.

By default, the dev server proxies `/api`, `/webui`, `/auth`, and WebSocket
traffic to `http://127.0.0.1:8765`.

If your gateway listens on a non-default port, point the dev server at it:

```bash
PYTHINKER_API_URL=http://127.0.0.1:9000 bun run dev
```

## Build for packaged runtime

```bash
cd webui
bun run build
```

This writes the production assets to `../pythinker/web/dist`, which is the
directory served by `pythinker gateway` and bundled into the Python wheel.

If you are cutting a release, run the build before packaging so the published
wheel contains the current WebUI assets.

## Test

```bash
cd webui
bun run test
```

## Per-message actions

Each message bubble exposes a hover-revealed action toolbar (also visible on
keyboard focus). The buttons are role-aware:

- **Stop** — composer-level button that replaces the send icon while the
  agent is generating. Click it to cancel the in-flight turn (forwards
  `/stop` to the agent loop's priority router).
- **Copy** (both roles) — writes the message text to the clipboard. Shows a
  transient checkmark for ~1.2s on success.
- **Regenerate** (assistant only) — drops the trailing assistant reply and
  re-runs the prior user turn. Always targets the *last* user message in the
  thread, even if you click regenerate on a middle assistant bubble.
- **Edit** (user only) — opens an inline editor with the message text.
  Save commits the change, truncates everything after that turn, and re-runs
  from there. Cancel restores the original. Save is disabled when the
  trimmed content equals the trimmed original (no-op guard).

The corresponding wire envelopes (`stop`, `regenerate`, `edit`) are handled
by the WebSocket channel handler in `pythinker/channels/websocket.py`.

## Visibility surfaces

Three small, additive indicators surface what the agent is doing:

- **Context-usage pill** — top of the thread header, right of the title.
  Renders `used / limit` (e.g. `12.4k / 200k`) with a thin progress bar.
  Turns amber above 75% and red above 90%. Refetches after every
  `stream_end` via `GET /api/sessions/<key>/usage` (served by the same
  WebSocket channel handler).
- **Tool-trace chips** — appear under the assistant turn whenever the
  agent invoked tools. One chip per unique tool kind (`shell`, `web_search`,
  …) with a count suffix when the same kind ran multiple times. Click the
  chevron to expand the full per-line trace; click again to collapse.
  Replaces the prior verbose trace listing with a compact summary that
  doesn't dominate the thread.
- **Latency subscript** — once a turn has been waiting for the first token
  for a full second, a `thinking… Ns` line appears next to the typing dots
  and ticks once per second until the stream begins. Hidden below 1s to
  avoid flicker on snappy turns.

## Power features

- **Slash-command palette**: typing `/` at the start of the composer opens
  a filtered list of every built-in command, sourced from
  `GET /api/commands` (which exposes `BUILTIN_COMMAND_METADATA`). Up/Down
  navigates, Enter or Tab fills the textarea with the command name and a
  trailing space, Esc closes.
- **Inline model switcher**: the model name in the composer footer is a
  dropdown that lists the active default plus any
  `agents.defaults.alternate_models` entries from
  `~/.pythinker/config.json`. Picking an alternate sets a per-chat
  override persisted on `Session.metadata['model_override']`. The agent
  loop reads it on every turn; "Use default" clears it. Same-provider
  switching only.

## Search & organize

- **In-chat search**: ⌘F / Ctrl+F opens an overlay that highlights every
  match in the open thread. ↓ / Enter steps forward, ↑ / Shift+Enter steps
  back, Esc closes. Highlights both user pills and assistant Markdown
  bodies (paragraphs, headings, list items, blockquotes, emphasis).
- **Cross-chat search**: the box above "Recent" scans every persisted chat
  for a substring match via `GET /api/search?q=...&offset=&limit=`.
  Debounced 200 ms, paginated 50 hits at a time (cap 200), backed by a
  read-only generator that walks every session's `history.jsonl` without
  resurrecting deleted sessions. Click a result to jump to that chat.
- **Pin / archive**: each chat row's overflow menu lets you pin a chat to
  the top of the sidebar or archive it out of view. Pinned, Recent, and
  Archived are three distinct sections; archived stays collapsed by
  default but archived chats still surface in cross-chat search results
  (with a small "archived" chip). Persisted as `pinned` / `archived`
  fields in a unified `<key>.meta.json` sidecar that also holds the chat
  title and any per-chat model override; legacy `<key>.title` files keep
  working until the first write of any field collapses the state.

## Polish & a11y

Phase 5 layers ergonomic and accessibility improvements across the surface:

- **Reduced-motion respect**: every `animate-*` / `transition-*` class is
  gated behind Tailwind's `motion-safe:` prefix, so users with
  `prefers-reduced-motion: reduce` see static dots, no slide-in, no
  pulsing cursor.
- **Per-message timestamps**: hovering a bubble reveals a subtle
  subscript — relative time (`2m ago`) for <24h, absolute date+time
  otherwise — with a tooltip showing the full ISO timestamp.
- **Drag-and-drop attachments**: drop image files anywhere on the chat
  area, not just the paperclip. The thread shell owns the drop zone and
  routes files through the same validator as paste/click attachment.
- **Reasoning drawer**: assistant turns containing `<think>...</think>`
  blocks render the chain-of-thought in a collapsed disclosure above the
  visible body. Copy actions never include the reasoning.
- **Mobile / iOS pass**: `100dvh` shell so the iOS Safari URL bar
  doesn't push the composer offscreen, ≥44px tap targets on touch
  viewports, and a `font-size: 16px` textarea minimum to prevent
  zoom-on-focus.
- **Keyboard shortcuts** (with `?` opening a help modal): `⌘K` new chat,
  `⌘/` toggle search overlay, `⌘↑` / `⌘↓` cycle chats, `Esc` cancel /
  close. Hotkeys are skipped while typing in inputs/textareas, except
  `Esc` which always fires.
- **Voice input**: a microphone button next to the paperclip records
  audio via the browser's `MediaRecorder`, sends it as a `transcribe`
  WebSocket envelope (base64-encoded webm/mp4/wav), and pastes the
  returned transcript into the composer. Active when
  `channels.transcription_provider` and the matching API key (Groq or
  OpenAI Whisper) are set in `~/.pythinker/config.json`; bootstrap's
  `voice_enabled` flag flips automatically. Disabled with an
  explanatory tooltip otherwise.

## Acknowledgements

- [`agent-chat-ui`](https://github.com/langchain-ai/agent-chat-ui) for UI and
  interaction inspiration across the chat surface.
