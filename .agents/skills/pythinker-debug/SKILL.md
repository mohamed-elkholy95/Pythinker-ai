---
name: pythinker-debug
description: Debug Pythinker runtime failures — bus stalls, provider errors, channel disconnects, sandbox issues, and test flakiness.
metadata:
  pythinker:
    emoji: "🔍"
    os: ["linux", "darwin", "windows"]
    requires:
      bins: ["uv"]
---

# Pythinker Debug Companion

Use when the user reports a runtime failure, unexpected behavior, or a test
that won't pass on a clean checkout.

## Session Kickoff

1. `git log --oneline -10` — recent context
2. `tasks/todo.md` and `tasks/lessons.md` if present
3. Pythinker version (`pythinker --version` or `pyproject.toml [project] version`)

## Diagnostic Entry Points

```bash
pythinker doctor          # always run first — full diagnostic
pythinker status          # runtime health, channel state, provider config
```

## Common Failure Modes

### Bus / Queue Stalls
**Files:** `pythinker/bus/queue.py`, `pythinker/channels/manager.py`,
`pythinker/agent/loop.py`

- Two unbounded `asyncio.Queue` objects (`MessageBus.inbound`/`outbound`)
- Channels publish `InboundMessage` (`pythinker/bus/events.py`)
- `ChannelManager._dispatch_outbound` drains outbound with stream-delta
  coalescing + retries (1 s, 2 s, 4 s)
- Per-session `asyncio.Lock` plus 20-slot pending queue
  (`pythinker/agent/loop.py:977` — `asyncio.Queue(maxsize=20)`)
- **Pending queue silently drops above 20.** Preserve that limit or update
  the doc explicitly.
- Session key: `"{channel}:{chat_id}"` unless
  `agents.defaults.unified_session=true`

### Provider Failures
**Files:** `pythinker/providers/base.py`,
`pythinker/providers/openai_compat_provider.py` (~1131 LOC),
`pythinker/providers/registry.py` (47 `ProviderSpec` entries),
`pythinker/providers/anthropic_provider.py`,
`pythinker/providers/azure_openai_provider.py`,
`pythinker/providers/openai_codex_provider.py`,
`pythinker/providers/github_copilot_provider.py`

- Per-model overrides in `OpenAICompatProvider`: DashScope `enable_thinking`,
  MiniMax `reasoning_split`, VolcEngine `thinking.type`, Moonshot
  `temperature=1.0`
- Responses-API circuit breaker
  (`pythinker/providers/openai_compat_provider.py:149-150`):
  `_RESPONSES_FAILURE_THRESHOLD=3`, `_RESPONSES_PROBE_INTERVAL_S=300`
  → 3 failures trip the breaker for 5 min
- Retry/backoff: 1, 2, 4 s — exponential. Long timeouts that look like
  provider hangs are usually backoff cumulative
- Role alternation, image stripping, retry/backoff all live in the
  `LLMProvider` base — don't duplicate per provider
- `F401`/`F841` lint errors can mask import failures in provider modules

### Channel Issues
**Files:** `pythinker/channels/*.py`

| Channel | File | Known quirks |
|---------|------|-------------|
| Telegram | `telegram.py` | `parse_mode` HTML pipeline breaks on nested tags |
| Slack | `slack.py` | `mrkdwn` fixup strips trailing `\n`; verify `thread_ts` propagation |
| Discord | `discord.py` | Webhook delivery; check `retry_after` on 429 |
| Matrix | `matrix.py` | Needs `libolm-dev`; missing → startup crypto errors |
| WhatsApp | `whatsapp.py` + `bridge/` | Baileys connection state; bridge is a thin Node relay |
| MS Teams | `msteams.py` | JWT validation; check token expiry |
| WebSocket | `websocket.py` (~1637 LOC) | Signed media URL secret regenerates on restart — old links 401 by design |
| Email | `email.py` | SMTP vs IMAP creds in `~/.pythinker/credentials/` |

### Tool Sandbox Failures
**File:** `pythinker/agent/tools/sandbox.py`

- Bubblewrap on Linux: Docker Compose needs `cap_add: SYS_ADMIN`,
  `apparmor: unconfined`, `seccomp: unconfined`
- **No network namespace isolation** — SSRF block-lists are the only
  egress mitigation
- Layout: workspace bind-rw, parent tmpfs-masked, media_dir bind-ro,
  `/usr /bin /lib /lib64 /etc/...` ro-bind-try, fresh `/proc /dev /tmp`
- Inspect with: `mount | grep bwrap`

### Browser Tool Failures
**Files:** `pythinker/agent/browser/manager.py`,
`pythinker/agent/browser/state.py`, `pythinker/agent/tools/browser.py`,
`pythinker/cli/doctor.py`

- Start with `pythinker doctor`; the Tools section reports whether browser
  tooling is disabled, whether Playwright is importable, whether CDP is
  reachable, and whether managed Chromium is installed
- Modes: `auto` launches managed Chromium unless a non-default `cdpUrl` is
  configured; `launch` forces managed Chromium; `cdp` requires a reachable
  DevTools endpoint
- CDP failures usually mean the `pythinker-browser` service is stopped, the
  wrong `tools.web.browser.cdpUrl` is configured, or Docker networking does
  not match the gateway container
- First-use hangs are usually Chromium provisioning. Check
  `tools.web.browser.autoProvision`, `provisionTimeoutS`, proxy variables, and
  `PLAYWRIGHT_DOWNLOAD_HOST`; retry manually with
  `python -m playwright install chromium`
- Launch-mode sandbox failures inside containers should recommend `cdp` mode
  first. `PYTHINKER_BROWSER_NO_SANDBOX=1` is an explicit local escape hatch,
  not the hardened default
- Contexts are per effective session key; restart/hot-reload closes contexts
  and carries a browser-restart notice into the next action
- SSRF blocks happen both on top-level `navigate` and context sub-requests;
  debug route-handler behavior in `pythinker/agent/browser/state.py`

### SSRF
**File:** `pythinker/security/network.py`

- `_BLOCKED_NETWORKS` covers RFC1918 + loopback + link-local + CGN +
  ULA + v6 equivalents
- Public API: `validate_url_target`, `validate_resolved_url`,
  `contains_internal_url`, `configure_ssrf_whitelist(cidrs)`
- Widen only via `tools.ssrf_whitelist`

### Grep Tool Catastrophic Backtracking
**File:** `pythinker/agent/tools/search.py` (`GrepTool`, line 253)

- User-supplied regex runs without a timeout
- Keep `output_mode` / `head_limit` / file-size guard rails
- Skip binaries, files >2 MB, output >128 000 chars

### Memory / Dream Issues
**File:** `pythinker/agent/memory.py` (~959 LOC; `MemoryStore`,
`Consolidator`, and the scheduled `Dream` class all live here)

- `dulwich` is pure-Python git — memory paths must not shell out to
  system `git`; the wheel works on hosts without git installed
- Per-workspace session repo; crash recovery via
  `_RUNTIME_CHECKPOINT_KEY` and `_PENDING_USER_TURN_KEY` in session
  metadata (`pythinker/agent/loop.py:189-190`)
- **Renaming either checkpoint key breaks crash recovery for live sessions.**
  Use a metadata migration if you must rename
- `Consolidator`: `_MAX_CONSOLIDATION_ROUNDS=5`, `_MAX_CHUNK_MESSAGES=60`
  (`pythinker/agent/memory.py:402-403`)
- Dream tool subset: `read_file`, `edit_file`, `write_file` only —
  no `spawn`, no `message`
- Stale-line annotation: `_STALE_THRESHOLD_DAYS=14`
  (`pythinker/agent/memory.py:659`) appends `← Nd` after lines older
  than 14 d

### Subagent Recursion
**Files:** `pythinker/agent/subagent.py`,
`pythinker/agent/tools/spawn.py`

- Subagents exclude `message` and `spawn` to prevent fan-out;
  re-adding them re-enables uncontrolled recursion
- `AgentRunner` runs subagents with `max_iterations=15` and
  `fail_on_tool_error=True` (`pythinker/agent/subagent.py:249`)
- Result is published as a system message via the bus with
  `session_key_override` so it lands in the originator's pending queue
  (mid-turn injection)

### Heartbeat Stalls
**File:** `pythinker/heartbeat/service.py`

- Default interval: 1800 s (`pythinker/config/schema.py:171`,
  `interval_s = 30 * 60`)
- Two-phase tick: `_decide` then `_tick`
- Reads workspace `HEARTBEAT.md`

## Concurrency / Streaming Knobs

- `PYTHINKER_MAX_CONCURRENT_REQUESTS` — global gate, default 3
- `PYTHINKER_STREAM_IDLE_TIMEOUT_S` — default 90 s

## Live Tests

- Check `~/.pythinker/config.json` and `~/.profile` for keys before
  assuming live tests are blocked
- Redact secret output; never commit real tokens, phone numbers, or
  chat IDs
- Live tests must be env-var gated

## Verification

```bash
uv run ruff check pythinker --select F401,F841   # CI gate (strictest)
uv run pytest tests/                              # full suite
uv run pytest tests/<subsystem>/                  # targeted

# When changing providers / channels
uv run pytest tests/providers/
uv run pytest tests/channels/
uv run pytest tests/tools/                        # tool dispatch / shell
uv run pytest tests/agent/tools/                  # tool unit tests
```

## Boundaries

- Provider/channel quirks: fix in the owner module first; add a generic
  core seam only when multiple owners need it
- Core loop stays extension-agnostic — no bundled provider/channel ids
- SSRF block-lists are mandatory; only widen via `tools.ssrf_whitelist`
- Bubblewrap sandbox is required on Linux for the shell tool — do not
  add bypasses
