# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pythinker (`pythinker-ai` on PyPI) is a lightweight, channel-agnostic personal-AI-agent framework written in Python with a React/TypeScript WebUI. One Python process ingests messages from chat platforms (Slack, Telegram, Discord, WhatsApp, Matrix, MS Teams, email, a WebSocket WebUI, and an OpenAI-compatible HTTP API), routes them through a shared agent loop, calls an LLM, runs tools, and replies on the originating channel.

## Development Commands

```bash
# Python — install dev deps (preferred, matches CI)
uv sync --all-extras

# Single test / pattern / full suite
uv run pytest tests/agent/test_runner.py::test_function -v
uv run pytest -k pattern
uv run pytest tests/

# Lint (CI is strictest about F401, F841)
uv run ruff check pythinker --select F401,F841
uv run ruff check pythinker
uv run ruff format pythinker             # not CI-enforced

# WebUI: dev server (proxies /api /webui /auth + WS to PYTHINKER_API_URL,
# default http://127.0.0.1:8765), build, test
cd webui && bun install
cd webui && bun run dev                  # http://127.0.0.1:5173
cd webui && bun run build                # writes to ../pythinker/web/dist (bundled into wheel)
cd webui && bun run test                 # vitest

# Node WhatsApp bridge (only if touching WhatsApp)
cd bridge && npm install && npm run build

# Run a gateway / API / interactive session
pythinker gateway                        # multi-channel + WebSocket, default :18790
pythinker serve                          # OpenAI-compatible HTTP, default 127.0.0.1:8900
pythinker agent                          # interactive CLI chat
```

CI matrix: `{ubuntu-latest, windows-latest} × {3.11, 3.12, 3.13, 3.14}`. Linux CI also `apt install libolm-dev build-essential` for the Matrix extra.

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`pythinker/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`pythinker/channels/`) receive messages from external platforms and publish `InboundMessage` events (`pythinker/bus/events.py`) onto `MessageBus.inbound`.
2. **`AgentLoop`** (`pythinker/agent/loop.py`, ~1685 LOC) consumes inbound messages under a per-session `asyncio.Lock`, builds context, and coordinates the turn. A 20-slot per-session pending queue lets subagent results and follow-on messages fold into an in-flight turn.
3. **`AgentRunner`** (`pythinker/agent/runner.py`, ~1116 LOC) executes the multi-turn LLM conversation: send messages to the provider, receive tool calls, dispatch tools, stream responses.
4. Responses are published as `OutboundMessage` events back to the bus; `ChannelManager._dispatch_outbound` (`pythinker/channels/manager.py`) drains them with stream-delta coalescing and 1 s / 2 s / 4 s retries.

Sessions are keyed `"{channel}:{chat_id}"` unless `agents.defaults.unified_session=true`. Mid-turn state is checkpointed into session metadata via `_RUNTIME_CHECKPOINT_KEY` and `_PENDING_USER_TURN_KEY` (`pythinker/agent/loop.py:189-190`) for crash recovery — **renaming either key breaks live sessions**.

### Key Subsystems

- **Agent loop** (`pythinker/agent/loop.py`, `runner.py`): core processing engine. Priority commands (`/stop`, `/restart`, `/status`) route pre-lock; the rest go through the per-session lock.
- **LLM providers** (`pythinker/providers/`): 47 `ProviderSpec` entries in `registry.py`. Most OpenAI-compatible vendors share `OpenAICompatProvider` with per-model override maps (DashScope `enable_thinking`, MiniMax `reasoning_split`, VolcEngine `thinking.type`, Moonshot `temperature=1.0`) and a Responses-API circuit breaker (`_RESPONSES_FAILURE_THRESHOLD=3`, `_RESPONSES_PROBE_INTERVAL_S=300`). Anthropic, Azure OpenAI, OpenAI Codex (OAuth), and GitHub Copilot (OAuth → token exchange) are dedicated subclasses. Retry/backoff (1, 2, 4 s), role alternation, and image stripping live in `LLMProvider` base.
- **Channels** (`pythinker/channels/`): platform integrations (Telegram, Slack, Discord, Matrix, WhatsApp, MS Teams, email, WebSocket). `manager.py` owns startup/shutdown + outbound dispatch; `registry.py` maps config name → class and supports the `pythinker.channels` entry point for third-party plugins.
- **Tools** (`pythinker/agent/tools/`): 16 tools — filesystem (`read_file`/`write_file`/`edit_file`/`list_dir`), search (`glob`/`grep`), shell (`exec`, `exclusive=True`), `notebook_edit`, `message`, `spawn`, `cron`, `mcp_*`, `my`, `web_search`/`web_fetch`. Registry + dispatch in `registry.py`; schema fragments in `schema.py`; ABC in `base.py`. Shell tool wraps user commands in bubblewrap on Linux (`pythinker/agent/tools/sandbox.py`).
- **Memory** (`pythinker/agent/memory.py`, ~959 LOC): `MemoryStore` is plain file I/O over `MEMORY.md` / `SOUL.md` / `USER.md` / `history.jsonl` in the session workspace. `Consolidator` compresses history under a token budget. `Dream` is a scheduled two-phase agent with a restricted tool subset (`read_file`, `edit_file`, `write_file`); edits auto-commit through `dulwich` (pure-Python git) so `/dream-log` and `/dream-restore` work.
- **Session management** (`pythinker/session/`): per-session history, context compaction, TTL-based auto-compaction.
- **Config** (`pythinker/config/schema.py`, `loader.py`): Pydantic `BaseSettings`. Disk is camelCase (`alias_generator=to_camel`); Python stays snake_case. `loader.py` recursively expands `${VAR}` tokens (raises if unset). Default path `~/.pythinker/config.json`; override with `set_config_path()`.
- **Security** (`pythinker/security/`): `network.py` enforces SSRF block-lists (RFC1918, loopback, link-local, CGN, ULA, v6 equivalents) — widen only via `tools.ssrf_whitelist`. Bubblewrap sandbox layout: workspace bind-rw, parent tmpfs-masked, media_dir bind-ro, fresh `/proc /dev /tmp`. **No network namespace isolation.**
- **Bridge** (`bridge/`): thin Node.js Baileys relay for WhatsApp. ALL business logic stays in Python. The wheel force-includes `bridge/src/` as `pythinker/bridge/` via `[tool.hatch.build.targets.wheel.force-include]`.
- **WebUI** (`webui/`): Vite + React 18 + TS 5.7 + Tailwind + Radix. Speaks the WebSocket multiplex protocol to `pythinker gateway`; REST surfaces (token issuance, bootstrap, sessions list, signed media URLs) live on the same port. Production build lands in `pythinker/web/dist` and is force-included into the wheel.

### Entry Points

All via the `pythinker` console script (defined in `pythinker/cli/commands.py`); `python -m pythinker ...` is equivalent.

| Command | Purpose |
|---------|---------|
| `pythinker onboard` | Interactive wizard that writes `~/.pythinker/config.json` |
| `pythinker agent` | Interactive CLI chat |
| `pythinker serve` | OpenAI-compatible HTTP server (default `127.0.0.1:8900`) |
| `pythinker gateway` | Multi-channel gateway + WebSocket server (default `:18790`) |
| `pythinker doctor` | Full diagnostic — always run first when triaging |
| `pythinker status` | Runtime health, channel state, provider config |

Python SDK entry point: `pythinker/pythinker.py`.

## Project-Specific Notes

- **Architecture deep dive**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — forensic walkthrough of the runtime spine.
- **Telegraph rules for AI coding agents**: [`AGENTS.md`](AGENTS.md) — root operational ruleset, mandatory before non-trivial changes.
- **Maintainer skill playbooks**: [`.agents/README.md`](.agents/README.md) — versioned `SKILL.md` workflows for debug, release, channel-test, provider-test. Not loaded by the runtime.
- **Security boundaries**: [`docs/security.md`](docs/security.md) — SSRF, sandbox, secrets, threat model.
- **User-facing docs**: [`docs/configuration.md`](docs/configuration.md), [`docs/deployment.md`](docs/deployment.md), [`docs/chat-apps.md`](docs/chat-apps.md), [`docs/memory.md`](docs/memory.md), [`docs/channel-plugin-guide.md`](docs/channel-plugin-guide.md).

## Branching Strategy

Two-branch model: **`main`** is stable (bug fixes, docs, minor tweaks auto-publish to PyPI on GitHub Release); **`dev`** is experimental (new features, breaking changes, refactors). When in doubt, target `dev`. Cherry-pick stable features from `dev` → `main`; never merge `dev` wholesale. Full PR rules in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Code Style

- **Python 3.11+**, asyncio throughout. `pytest` runs with `asyncio_mode = "auto"` — `async def test_...` just works (no `@pytest.mark.asyncio`).
- **Line length**: 100 (target).
- **Linting**: `ruff` with rule groups `E, F, I, N, W`. **`E501` is ignored** (long strings allowed). CI is strictest about `F401` (unused imports) and `F841` (unused variables) — clean both before opening a PR.
- **Logging**: `from loguru import logger`. Do not use stdlib `logging`.
- **Tests mirror runtime layout**: `tests/agent/`, `tests/agent/tools/`, `tests/channels/`, `tests/providers/`, `tests/cli/`, `tests/cron/`, `tests/security/`, `tests/session/`, `tests/tools/`, `tests/utils/`, `tests/command/`, `tests/config/`. New behavior needs a test; bug fixes include a test that fails before and passes after.
- **No drive-by refactors.** Match surrounding style. The project explicitly values a small, readable core — see `CONTRIBUTING.md`.

## Common File Locations

| Surface | Path |
|---------|------|
| Bus / event types | `pythinker/bus/queue.py`, `pythinker/bus/events.py` |
| Agent loop / runner | `pythinker/agent/loop.py`, `pythinker/agent/runner.py` |
| Memory / Dream / Consolidator | `pythinker/agent/memory.py` |
| Tool registry / base / schema | `pythinker/agent/tools/registry.py`, `base.py`, `schema.py` |
| Sandbox (bubblewrap) | `pythinker/agent/tools/sandbox.py` |
| SSRF / network guards | `pythinker/security/network.py` |
| Provider base / template | `pythinker/providers/base.py` |
| Provider registry | `pythinker/providers/registry.py` |
| OpenAI-compatible provider | `pythinker/providers/openai_compat_provider.py` |
| Channel base / template | `pythinker/channels/base.py` |
| Channel manager / dispatch | `pythinker/channels/manager.py` |
| Channel registry | `pythinker/channels/registry.py` |
| Config schema | `pythinker/config/schema.py` |
| Config loader (`${VAR}` expansion) | `pythinker/config/loader.py` |
| Path helpers | `pythinker/config/paths.py` |
| Skills loader (runtime) | `pythinker/agent/skills.py` |
| Bundled runtime skills | `pythinker/skills/` |
| Maintainer skills (not runtime) | `.agents/skills/` |
| Skill validator (canonical) | `pythinker/skills/skill-creator/scripts/quick_validate.py` |
| CLI commands | `pythinker/cli/commands.py` |
| Onboarding wizard | `pythinker/cli/onboard.py` |
| Python SDK facade | `pythinker/pythinker.py` |
| WebUI dev proxy config | `webui/vite.config.ts` |
| WhatsApp bridge | `bridge/src/` (force-included into wheel as `pythinker/bridge/`) |

## Agent Skills

Two skill trees live in this repo and they serve different audiences. Don't mix them.

### Runtime skills — `pythinker/skills/`

Loaded by `pythinker/agent/skills.py` (`SkillsLoader`) at runtime. Resolution order is **only**:

1. Workspace skills: `<workspace>/skills/` (shadow built-ins of the same name)
2. Bundled built-ins: `pythinker/skills/` (ships in the wheel)

Always-on built-ins (`always: true` frontmatter): `memory`, `my`. Anything an end user might invoke from their workspace belongs here.

### Maintainer skills — `.agents/skills/`

Versioned playbooks for AI coding agents working **on Pythinker**. They are **not** loaded by the runtime — `SkillsLoader` does not look at `.agents/` at all. See [`.agents/README.md`](.agents/README.md) for the full anchor.

Each skill is a `SKILL.md` validated by the canonical `pythinker/skills/skill-creator/scripts/quick_validate.py`. Allowed frontmatter keys: `name`, `description`, `metadata`, `always`, `license`, `allowed-tools`. Optional resource dirs: `scripts/`, `references/`, `assets/`. Pythinker-specific metadata lives under `metadata.pythinker.*` (`emoji`, `os`, `requires.bins`, `requires.env`, `install`).

Current maintainer skills: `pythinker-debug`, `pythinker-release`, `pythinker-channel-test`, `pythinker-provider-test`. Add complex operational tasks (GHSA triage, secret scanning, schema migrations, testing rigs) here — never in `pythinker/skills/`.

Validate locally:

```bash
uv run python .agents/scripts/validate_skills.py
uv run pytest tests/test_agents_skills.py
```

The CI guard at `tests/test_agents_skills.py` catches both shape errors and stale module references in cited file paths.

## Release Pipeline

PyPI and TestPyPI are wired to GitHub Actions Trusted Publishing (OIDC, no tokens) via `.github/workflows/publish.yml`. Triggered by `release: published` (auto → PyPI) or `workflow_dispatch` with `target: pypi|testpypi`.

**Version bumps must update two files in lockstep** — otherwise source checkouts drift from published metadata:

- `pyproject.toml` → `[project] version = "..."`
- `pythinker/__init__.py` (~line 24) → the fallback literal in `_read_pyproject_version() or "..."`

Zero-touch release:

```bash
git commit -am "release 0.1.1" && git tag v0.1.1 && git push --follow-tags
gh release create v0.1.1 --generate-notes
```

Manual TestPyPI dry-run: `gh workflow run publish.yml -f target=testpypi --ref main`.

The README PyPI badge (`README.md:9`) uses `https://img.shields.io/pypi/v/pythinker-ai` — shields.io fetches the live version from PyPI's JSON API, so it always reflects the latest publish. **Never hardcode a version.** Tag-vs-`pyproject` mismatch is enforced at publish time by the workflow's "Resolve package version" step — don't bypass it.

Maintainer playbook: [`.agents/skills/pythinker-release/SKILL.md`](.agents/skills/pythinker-release/SKILL.md).

## Docker

`docker-compose.yml` exposes three services:

- `pythinker-gateway` on `:18790`
- `pythinker-api` on `127.0.0.1:8900`, workspace `/home/pythinker/.pythinker/api-workspace`
- `pythinker-cli` (profile-gated, interactive)

All services run `cap_drop: ALL` + `cap_add: SYS_ADMIN` (needed by bubblewrap namespaces) and `apparmor/seccomp: unconfined`. The image layers Node 20 on top of `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, builds the WhatsApp bridge, and runs as non-root `pythinker:1000`.
