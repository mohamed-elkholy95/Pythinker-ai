# CLAUDE.md

Claude Code compatibility entrypoint. The canonical, agent-neutral rules live in [`AGENTS.md`](AGENTS.md) and are loaded below via `@`-import so this file matches what Codex, OpenCode, and Pi already read.

## Canonical instructions

@AGENTS.md

## Scoped subtree rules

Claude Code auto-loads the nearest `AGENTS.md` when working inside a subtree. Scoped files:

- [`bridge/AGENTS.md`](bridge/AGENTS.md) — WhatsApp relay (Node/Baileys).
- [`webui/AGENTS.md`](webui/AGENTS.md) — Vite/React frontend.
- [`pythinker/providers/AGENTS.md`](pythinker/providers/AGENTS.md) — provider adapters and quirks.
- [`pythinker/channels/AGENTS.md`](pythinker/channels/AGENTS.md) — chat-platform adapters.
- [`pythinker/agent/tools/AGENTS.md`](pythinker/agent/tools/AGENTS.md) — built-in tools and MCP.
- [`pythinker/templates/AGENTS.md`](pythinker/templates/AGENTS.md) — **published runtime surface** for end-user workspaces; not a contributor guide.

## Local overlays

@CLAUDE.local.md
@AGENTS.override.md

Both files are gitignored. Missing files are silently skipped by Claude Code's `@`-import. `AGENTS.override.md` is the shared overlay also read by Codex (per Codex docs); `CLAUDE.local.md` remains Claude-specific. Keep durable rules in `AGENTS.md`, not in overlays.

Do not duplicate rules in this file. Update `AGENTS.md` or the relevant scoped `AGENTS.md` instead.
