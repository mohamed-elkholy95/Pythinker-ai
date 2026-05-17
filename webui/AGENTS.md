# AGENTS.md

Scoped rules for `webui/`. Root [`../AGENTS.md`](../AGENTS.md) applies first. Keep this file WebUI-specific only.

## Scope

Browser front-end for the Pythinker gateway: Vite + React 18 + TypeScript + Tailwind 3 + shadcn/ui. Production build writes to `../pythinker/web/dist` and ships in the wheel.

## Commands

```bash
bun install                  # preferred; bun.lock is canonical
bun run dev                  # http://127.0.0.1:5173, proxies to gateway
bun run build                # tsc first, then writes ../pythinker/web/dist
bun run test                 # vitest, single run
bun run test:watch
bun run lint                 # Biome lint
bun run format               # Biome format src/

bunx vitest run src/tests/pythinker-client.test.ts
bunx vitest run -t "regenerate"
bunx tsc -p tsconfig.build.json --noEmit
```

No `package-lock.json` or `pnpm-lock.yaml` next to `bun.lock`.

## Local gateway

Dev server proxies `/api`, `/webui`, `/auth`, and WebSocket upgrades on `/` to `PYTHINKER_API_URL` (default `http://127.0.0.1:8765`). HMR uses port `5174`.

```bash
PYTHINKER_API_URL=http://127.0.0.1:9000 bun run dev
```

For a live backend, start `pythinker gateway` with `channels.websocket.enabled=true` first.

## Architecture

Boot path: `src/main.tsx` -> `src/App.tsx` -> `fetchBootstrap()` -> `PythinkerClient` -> `ClientProvider`.

- `src/lib/pythinker-client.ts`: shared WebSocket owner; reconnect, token refresh, chat-id routing.
- `src/hooks/usePythinkerStream.ts`: per-chat streaming reducer.
- `src/lib/api.ts`: chat REST helpers.
- `src/lib/admin-api.ts`: admin/config diagnostics helpers.
- `src/lib/types.ts`: authoritative TypeScript view of the WebSocket protocol.

Frames without `chat_id` bypass `onChat`; wire a dedicated handler or they are silently dropped.

## Components

- `src/components/Sidebar.tsx`: chat navigation, pin/archive, search.
- `src/components/thread/`: chat viewport, header, composer, message rendering.
- `src/components/admin/`: config/admin dashboard.
- `src/components/ui/`: shadcn/ui primitives; treat as generated/vendor-style code unless directly needed.

## Cross-cutting rules

- Path alias `@/*` -> `src/*`.
- i18n via `react-i18next`; locales under `src/i18n/locales/`.
- Markdown uses `streamdown` + remark/rehype stack through `MarkdownText`; call `preloadMarkdownText()` when new boot code depends on the lazy bundle.
- `<think>...</think>` blocks are stripped from copy and rendered in `ReasoningDrawer`.
- Image attachments encode in `src/workers/imageEncode.worker.ts`.
- Keep `@radix-ui/react-dialog` excluded from Vite dep optimization.
- Keep Rollup manual chunking that collapses Radix into `radix-ui`.

## Tests

Vitest + `happy-dom`, globals on, setup in `src/tests/setup.ts`. Tests live in `src/tests/`. Match existing WebSocket-client mocks in `pythinker-client*.test.ts` and `usePythinkerStream*.test.tsx`.

## Footguns

- Wire envelope changes must update server-side handling in `pythinker/channels/websocket.py` in the same change.
- Do not hand-edit `../pythinker/web/dist`; rebuild with `bun run build`.
