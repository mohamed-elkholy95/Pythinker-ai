# CLAUDE.md

Claude Code compatibility entrypoint.

Canonical repo instructions live in [`AGENTS.md`](AGENTS.md). Read that file before work. Scoped subtree instructions live in subtree `AGENTS.md` files, including:

- [`bridge/AGENTS.md`](bridge/AGENTS.md)
- [`webui/AGENTS.md`](webui/AGENTS.md)
- [`pythinker/templates/AGENTS.md`](pythinker/templates/AGENTS.md)

If `CLAUDE.local.md` exists at the repo root, it is also loaded as a local-only overlay (gitignored, machine-specific notes). Precedence: canonical `AGENTS.md` → scoped subtree `AGENTS.md` → `CLAUDE.local.md` overlay.

Do not duplicate rules here. Update `AGENTS.md` or the relevant scoped `AGENTS.md` instead.
