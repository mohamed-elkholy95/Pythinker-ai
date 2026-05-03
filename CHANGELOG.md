# Changelog

All notable user-visible changes to Pythinker land here. The project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.1] - 2026-05-03

### Fixed

- **WebUI: empty stream-end placeholders no longer linger.** Reasoning
  models that emit only `<think>...</think>` text on the wire before a
  tool call (DeepSeek-R1, MiniMax `reasoning_split`, VolcEngine
  `thinking`) used to leave a blank assistant bubble after the stream
  closed; the WS stream end now drops messages whose visible delta
  text is empty.
- **Agent: `<think>` blocks are now stripped from generated chat
  titles.** Reasoning models occasionally prepended chain-of-thought to
  the title prompt response; `_clean_title` now removes any
  `<think>...</think>` span before quote/punctuation trimming so titles
  read cleanly in the sessions list.

### Changed

- **Internal lint hygiene.** Test tree now passes the full ruff rule
  set (E, F, I, N, W) cleanly; `pytest.mark.timeout` is registered;
  `asyncio.wait_for(coro, timeout=...)` in `pythinker/agent/loop.py` is
  replaced with the recommended `async with asyncio.timeout(...)` form
  to suppress a spurious `RuntimeWarning: coroutine 'Queue.get' was
  never awaited`. No public API change.
- **PyPI metadata.** Author now resolves cleanly to `Mohamed Elkholy
  <moelkholy1995@gmail.com>`; project Homepage now points to
  `https://pythinker.com`.

## [2.1.0] - 2026-05-02

### Added

- **Config Workbench admin UI.** The Admin Config tab now ships a
  schema-driven workbench replacing the previous dotted-path text editor.
  Includes a left-rail service navigator, 11 service dashboards, secret
  rotation modal, full-text search, pending-diff drawer, provider matrix,
  channel grid, and SSRF coverage bar
  (`webui/src/components/admin/config/ConfigWorkbench.tsx`,
  `webui/src/components/admin/config/SchemaForm.tsx`).
- **Smoother streaming in the WebUI.** `usePythinkerStream` now coalesces
  WebSocket deltas through `requestAnimationFrame`, collapsing bursty
  token chunks into one render per frame. All cleanup paths (stream_end,
  error, stop, regenerate, edit, chat-switch) cancel the pending frame
  and flush trailing text.
- **Streamdown-powered markdown rendering.** `MarkdownText` swaps
  `react-markdown` for Streamdown — block-level memoization,
  `parseIncompleteMarkdown`, per-word `fadeIn` animation gated on
  `isStreaming` and a JS-side `useReducedMotion`.
- **Stick-to-bottom scroll behavior.** `ThreadViewport` replaces the
  hand-rolled `scrollTo` + `NEAR_BOTTOM_PX` heuristic with
  `use-stick-to-bottom` (ResizeObserver + spring). Single upward flick
  disengages auto-follow cleanly; the floating pill calls
  `scrollToBottom('smooth')`.
- **Reasoning-aware typing dots.** `MessageBubble` now gates the
  typing-dots placeholder on the post-`extractThink` visible text so
  reasoning-model deltas (DeepSeek-R1, Claude extended thinking, MiniMax
  `reasoning_split`, Volcengine `thinking`) keep the dots bouncing
  until the actual answer starts streaming. The reasoning drawer
  renders alongside the dots so users can watch reasoning roll in.
- **Usage tab redesign.** `admin/UsageView` now ships segmented Summary
  / Sessions / Ledger sub-views, four tone-tinted KPI cards, an SVG
  donut + segment legend for the last-turn breakdown (auto-includes
  provider extras like cached_input/reasoning tokens), a top-sessions
  panel with color-escalated context-window bars, channel rollup with
  gradient bars, and a full sessions table with progress bars. Raw
  ledger preserved under its own view.
- **Package-first browser tool launch mode.** `playwright` now ships with the
  default install, `tools.web.browser.mode` supports `auto` / `launch` / `cdp`,
  launch mode can lazily provision Playwright Chromium, and `pythinker doctor`
  reports browser configuration and provisioning status.
- **Browser runtime lifecycle controls.** Browser contexts now enforce idle
  eviction, optional browser disconnect-on-idle, per-context page limits, and
  next-turn hot reload when browser config changes.
- **Browser maintainer guidance.** Runtime prompt templates now distinguish
  `web_fetch` from headless Chromium browser automation, and maintainer skills
  cover browser triage and verification paths.

### Changed

- **`pythinker-ai[browser]` compatibility alias.** The extra remains accepted
  for older install commands, but no longer adds packages because Playwright is
  part of the base dependency set.

### Fixed

- **First-use Chromium provisioning no longer blocks other chats.**
  `BrowserSessionManager._ensure_browser` now releases its connect lock
  while `python -m playwright install chromium` runs, so a 30-300 s
  provisioning subprocess on chat A cannot stall chat B's `acquire()`.
- **`auto` mode falls back to launch mode when an explicit `cdpUrl` is
  unreachable.** Now covered by a regression test
  (`test_auto_mode_falls_back_to_launch_when_configured_cdp_unreachable`).
- **`shutdown(force=True)` actually skips per-context cleanup.** Used by
  the hot-reload 10 s deadline path so a hanging
  context-close cannot block configuration changes from taking effect.
- **`pythinker doctor` no longer crashes when invoked from inside a
  running event loop.** The CDP probe is skipped with a `warn`
  result instead of raising `asyncio.run() cannot be called from a
  running event loop`.

## [2.0.2] - 2026-05-01

### Fixed

- **Onboarding wizard — Use existing config short-circuits to outro.**
  Picking *Use existing* now jumps straight to the final step instead of
  walking the user through provider/channel/search/save prompts that would
  overwrite the on-disk config (`pythinker/cli/onboard.py:339-361`,
  `:1459`).
- **Onboarding OAuth login passes the registry id, not the display name.**
  `_step_run_auth` now hands `_login_via_oauth_remote` the canonical
  `spec.name` (e.g. `openai_codex`) instead of the human-readable
  `ctx.auth` label, fixing browser-login failures for providers whose
  display name and registry id differ
  (`pythinker/cli/onboard.py:706`).
- **"Saved" vs "Updated" wording for fresh installs.** First-time saves
  log `Saved <path>` instead of misleadingly reporting an update
  (`pythinker/cli/onboard.py:1334`).

### Changed

- **Existing-config and pre-save summaries iterate the provider registry.**
  `render_existing_summary` and `render_pre_save` no longer rely on a
  hard-coded shortlist; any provider declared in `PROVIDERS` is now
  detected, and channel configs stored as dict extras are correctly
  reported as enabled (`pythinker/cli/onboard_views/summary.py`).

## [2.0.1] - 2026-05-01

### Added

- **Admin dashboard** — tabbed web UI surface (`/admin`) with Overview,
  Config, Sessions, and Usage tabs; powered by new `pythinker/admin/service.py`
  and `webui/src/components/admin/AdminDashboard.tsx`.
- **`UsageLedger`** — per-session token and cost accounting
  (`pythinker/agent/usage_ledger.py`) surfaced in the admin Usage tab.
- **Config editing API** — `pythinker/config/editing.py` exposes
  `read_config_value`, `set_config_value`, `unset_config_value`, and
  `save_config_with_backup` for safe, auditable config mutations from the
  admin UI or CLI.
- **WebSocket admin endpoints** — `/admin/config`, `/admin/sessions`,
  `/admin/usage` REST surfaces on the gateway port; token-gated so only
  authenticated clients can mutate config.
- **TUI: Tavily web-search explanation** — `/mcp` screen now distinguishes
  Tavily's MCP server from the built-in `web_search` tool and explains when
  to use each.

### Changed

- **WebUI enabled by default** on local loopback (`127.0.0.1:8765`); the
  admin surface stays hidden behind the bearer token on external interfaces.
- **TUI message queue** — messages received during an active agent turn are
  queued (up to 20) and processed after the turn completes rather than
  being dropped.
- **TUI scrolling** — auto-scroll and cursor positioning stabilised; chat
  pane no longer fights the scroll position on every render.
- **TUI MCP config** — `/mcp` screen syncs provider state from disk on
  each open instead of caching the initial snapshot.

### Fixed

- `commands.py`: `suggested_target_command` and `target_install_command`
  were used but not imported — caused `NameError` at runtime on
  `pythinker update --target VERSION`.
- `onboard.py`: loop variable `field` shadowed `dataclasses.field` import.
- Import ordering in `admin/service.py`, `agent/loop.py`, and
  `runtime/` modules (ruff I001/W291).

## [2.0.0] - 2026-04-30

First major release past the `0.1.x` line. Skipping `1.x` is intentional:
the runtime + UI rewrite earns the `2.0.0` cut directly. **Major-version
upgrades are not auto-installed** — `pythinker upgrade` will refuse to
cross from `1.x → 2.x` and prompts for `pythinker update --target 2.0.0`.

### Added

- **`pythinker tui` (alias `chat`)** — full-screen `prompt_toolkit` chat
  with persistent pane, live streaming, slash-command pickers (`/model`,
  `/provider`, `/sessions`, `/theme`, `/help`, `/status`), fuzzy search,
  Ctrl+C cancellation of in-flight turns, default + monochrome themes,
  Claude-Code-style welcome card.
- **Governed-execution runtime** *(off by default)* — `RuntimeConfig`
  schema (`policyEnabled`, `telemetrySink`, `sessionCacheMax`, `max*`,
  `manifestsDir`, `defaultAgentId`, `blockedSenders`), `PolicyService`
  (allow-lists, budgets, recursion depth), `ToolEgressGateway` chokepoint,
  `AgentRegistry` + `AgentManifest` directory loader, `RequestContext` +
  `BudgetCounters` spine primitive, pluggable `TelemetrySink` (loguru /
  JSONL / composite). Same-shape configuration is bit-for-bit identical
  to the legacy path when `runtime.policyEnabled` is unset.
- **Provider hot-reload** — `AgentLoop` accepts `provider_snapshot_loader`
  + `provider_signature`; `_refresh_provider_snapshot()` runs at the top
  of every `_process_message`. Edits to model / provider / api_key in
  `~/.pythinker/config.json` cascade through the runner, subagent
  manager, consolidator, and dream at the next turn boundary without
  restarting the SDK or the gateway.
- **Research-grade PDF reports** — `make_pdf` agent tool renders
  structured Markdown to a styled PDF via ReportLab. Optional `[reports]`
  extra: `pip install 'pythinker-ai[reports]'`.
- **Onboard wizard polish** — MiniMax token-plan flow, error/summary
  views, save-on-back, sensitive-field masking, Telegram "Allow From" →
  "Allowed IDs" rename, `--non-interactive --flow quickstart` end-to-end.
- **Release-readiness gate** — `pythinker release check` runs the same
  battery as `publish.yml`: PEP 440 version validity, `pyproject.toml ↔
  pythinker/__init__.py` fallback equality, `CHANGELOG.md` per-version
  section presence, git-tag/pyproject equality, optional `--build`
  (python -m build, twine check, wheel filename verification).
- **Updater UX** — `pythinker update --target VERSION` for exact-version
  installs across `uv tool` / `pipx` / `pip` venv, with refusals printed
  for editable / container / system-pip paths. `pythinker upgrade` keeps
  its "latest stable" semantics but now refuses to cross a major version
  without explicit `--target` opt-in.
- **Auth** — per-provider refresh-token file lock; `oauth_remote` refresh
  resilience.
- **Provider plumbing** — factory hardening; extra-body config support
  for `github_copilot` / `openai_codex` / `openai_compat`.

### Changed

- **`AgentLoop.__init__`** accepts new optional kwargs: `provider_snapshot_loader`,
  `provider_signature`, `policy`, `runtime_config`, `session_cache_max`. All
  default to safe values; embedders that pass kwargs **positionally** must
  adapt.
- **`Pythinker.from_config`** (SDK facade) wires hot-reload + governed-execution
  by default. Programs constructing `AgentLoop` directly opt out by leaving
  the new kwargs at their defaults.
- **API server** — typed `web.AppKey` storage migration; `PermissionError`
  → 403 on the non-streaming + streaming pre-authorize paths; SDK traffic
  labelled `channel="sdk"` (was `channel="api"`).
- **CI** — `astral-sh/setup-uv` SHA-pinned; GitHub Actions Node 24-ready
  bumps; `publish.yml` purges GitHub camo cache after each release so
  shields.io PyPI badges refresh in minutes instead of hours.

### Deprecated

- Hardcoded `pythinker/__init__.py` fallback drift now fails
  `pythinker release check`. Bump both files in lockstep — see
  `docs/cli-reference.md` `pythinker release` for details.

### Migration notes

| Audience | Action |
|---|---|
| Stable-only users | `python -m pip install --upgrade pythinker-ai` will **not** auto-jump from `1.x` to `2.0.0`. Run `pythinker update --target 2.0.0 -y` (or `python -m pip install --force-reinstall "pythinker-ai==2.0.0"`) to opt in. |
| Embedders calling `AgentLoop.__init__` positionally | Switch to keyword arguments. The new kwargs (`provider_snapshot_loader`, `policy`, …) are appended after `tools_config` and have safe defaults. |
| Operators pinning `runtime.*` config | The runtime layer is **off by default**. To enable: set `runtime.policyEnabled=true` and either (a) set `runtime.manifestsDir` to a directory of `AgentManifest` YAML files, or (b) opt into `runtime.policyMigrationMode="allow-all"` for an explicit allow-all bridge. Without either, every tool call is denied — this is intentional. |
| Source checkouts | `pythinker/__init__.py` and `pyproject.toml` must agree. Run `pythinker release check` after any version bump. |

### Removed

- Stale documentation references to the `pdf` extra for `make_pdf` — the
  branded report tool ships under `[reports]`. The `[pdf]` extra remains
  for read-only PyMuPDF-backed PDF text extraction.

## [Unreleased — pre-2.0.0 WebUI overhaul, captured for history]

This entry covers the WebUI overhaul that promotes the browser surface from a
minimum-viable chat into a daily-use surface. Five focused phases shipped on
the `port/cli` branch; everything is gated to the `pythinker gateway`
process and consumes existing channel-manager seams without introducing new
transports or processes.

### Added — WebUI

#### Conversational control (Phase 1)

- Per-message actions toolbar on hover/focus: **Copy** (both roles),
  **Regenerate** (assistant turns), and **Edit** (user turns; opens an inline
  editor that truncates the thread and re-runs from the rewritten message).
- Composer **Stop** button replaces the send icon while a turn is streaming;
  forwards `/stop` to the agent loop's priority router. Cancels mid-turn
  cleanly without orphaning typing-dots placeholders.
- New WebSocket envelopes: `stop`, `regenerate`, `edit`. Backend routes
  regenerate/edit through the priority command queue to coordinate with the
  session lock.

#### Visibility (Phase 2)

- **Context-usage pill** in the thread header showing `used / limit` tokens
  with a thin progress bar (amber > 75%, red > 90%). Refetches after every
  `stream_end`; backed by `GET /api/sessions/<key>/usage`.
- **Tool-trace chips** under each assistant turn — one chip per unique tool
  kind with a `× N` count suffix when repeated. Click the chevron to expand
  the full trace lines. Replaces the prior verbose listing.
- **Latency subscript** ("thinking… Ns") next to the typing dots, shown only
  after the first second to avoid flicker on snappy turns.

#### Power features (Phase 3)

- **Slash-command palette**: typing `/` at the start of the composer opens a
  filterable list of every built-in command (sourced from
  `BUILTIN_COMMAND_METADATA` via a new `GET /api/commands`). Up/Down
  navigates, Enter or Tab fills the textarea with `<name> `, Esc closes.
- **Inline model switcher**: the model name in the composer footer is now a
  dropdown listing the configured default plus
  `agents.defaults.alternate_models`. Selection sets a per-chat override
  persisted on `Session.metadata['model_override']`. The agent loop honors it
  per turn.
- New routes: `GET /api/commands`, `GET /api/models`. New envelope:
  `set_model`. New config field: `agents.defaults.alternate_models`.

#### Search & organize (Phase 4)

- **In-chat search** (`⌘F` / `Ctrl+F`): overlay highlights every match in
  the open thread. ↓ / Enter steps forward, ↑ / Shift+Enter steps back, Esc
  closes. Highlights both user pills and assistant Markdown bodies
  (paragraphs, headings, list items, blockquotes, emphasis). Each match has
  a unique `data-match-id` so navigation is unambiguous across siblings.
- **Cross-chat search**: a debounced (200ms) input above "Recent" scans
  every persisted session's `history.jsonl`. Backed by `GET /api/search`
  with `?q=&offset=&limit=` pagination (default 50, cap 200) and a flat
  result list with snippet highlighting. Click a hit to jump to the chat
  and scroll directly to the matched message.
- **Pin / archive**: each chat row's overflow menu offers Pin and Archive.
  Sidebar renders three sections: Pinned, Recent, Archived (collapsed by
  default). Archived chats stay in cross-chat search results with a small
  "archived" chip.
- New routes: `GET /api/search`, `GET /api/sessions/<key>/pin`,
  `GET /api/sessions/<key>/archive` (toggles).
- **Sidecar consolidation**: `<key>.title` legacy sidecar is folded into a
  unified `<key>.meta.json` carrying `{title, pinned, archived,
  model_override}`. Legacy `.title` files keep working until the first
  write of any other field collapses state. Migration is invisible.

#### Polish & a11y (Phase 5)

- **Reduced-motion respect**: every `animate-*` / `transition-*` class is
  gated behind Tailwind's `motion-safe:` so users with
  `prefers-reduced-motion: reduce` see static UI.
- **Per-message timestamps**: subscript appears on hover under each bubble
  (relative `2m ago` for <24h, absolute date+time otherwise; full ISO in a
  tooltip).
- **Drag-and-drop attachments**: drop image files anywhere on the chat
  area, not just the paperclip. Reuses the existing validator pipeline.
- **Reasoning drawer**: assistant turns containing `<think>...</think>`
  blocks render the chain-of-thought in a collapsed disclosure above the
  visible body. Copy actions never include the reasoning.
- **Mobile / iOS pass**: `100dvh` shell so the iOS Safari URL bar doesn't
  push the composer offscreen; ≥44px tap targets on touch viewports;
  `font-size: 16px` textarea minimum to prevent zoom-on-focus.
- **Global keyboard shortcuts** with `?` opening a help modal: `⌘K` new
  chat, `⌘/` toggle search, `⌘↑` / `⌘↓` cycle chats, `Esc` cancel/close.
  Hotkeys are skipped while typing in inputs/textareas, except `Esc` which
  always fires.
- **Voice input**: a microphone button next to the paperclip records audio
  via `MediaRecorder` and sends it as a `transcribe` WebSocket envelope
  (base64-encoded webm/mp4/wav). The backend writes to a temp file,
  dispatches to the configured `transcription_provider` (Groq or OpenAI
  Whisper, both already in `pythinker/providers/transcription.py`), and
  emits `transcription_result`. Active when
  `channels.transcription_provider` and the matching API key are set in
  `~/.pythinker/config.json`; bootstrap's `voice_enabled` flag flips
  automatically.

### Added — Backend / agent

- `MessageBus`-coordinated regenerate/edit flow that drops the trailing
  assistant turn, truncates after the target user message, and re-runs.
- `Session.metadata['model_override']` consumed at every turn — per-chat
  model override without bypassing the configured provider.
- `MEMORY.md` / `<key>.meta.json` JSON sidecar replaces the legacy plain
  `<key>.title` file.
- `iter_message_files_for_search` read-only generator (does not resurrect
  deleted sessions or mutate `_cache`).
- `BUILTIN_COMMAND_METADATA` single-source-of-truth constant; existing
  `build_help_text()` derives from it instead of duplicating the list.
- Cross-chat search helper (`search_sessions` + `build_snippet`) — pure
  substring match, paginated, deterministic ordering.

### Fixed

- Regenerate/edit no longer leak deltas from a cancelled stream into the
  next placeholder bubble.
- Empty edit content is rejected client-side and server-side.
- `format_tool_hints`-shaped trace lines parse correctly into
  `<ToolTraceChips>` (the original colon-split assumption was wrong; verbs
  are now the first whitespace-or-paren token, with `× N` repeats
  preserved).
- `messageBubbleSearch` test no longer flakes under default
  `vitest run` parallel mode (preloads the lazy `MarkdownTextRenderer`
  chunk so Suspense doesn't race `waitFor`).

### Removed

- `webui/src/components/ChatPane.tsx`, `Composer.tsx`, `MessageList.tsx` —
  unreferenced scaffolding superseded by `ThreadShell` + `ThreadComposer`
  during Phases 1–5 (-353 LOC). Flagged in the Phase 5 plan self-review.

### Notes

- This branch (`port/cli`, 79 commits) has not yet been merged
  to `main` or `dev`. No live browser smoke pass run — `vitest`
  (186 passing) and `pytest` (2235 passing, 1 skipped) cover the unit
  and integration layers.

[Unreleased]: https://github.com/mohamed-elkholy95/Pythinker-ai/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/mohamed-elkholy95/Pythinker-ai/releases/tag/v2.0.0
