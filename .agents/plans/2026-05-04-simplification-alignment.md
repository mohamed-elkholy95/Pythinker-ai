# Simplification & Alignment Plan — 2026-05-04

> **Maintainer-only.** Lives under `.agents/plans/` per
> `.agents/README.md` § Boundaries: maintainer execution plans must not
> sit in public `docs/`. Do not link to this from `README.md`,
> `AGENTS.md`, the wheel, or any user-facing surface.
>
> **Errata.** Four correction passes have landed; the full changelog is in §15.
> In-line `**Correction.**` blocks mark spots where an earlier draft was wrong.
>
> **Status:** Draft. Comprehensive file-by-file scan of Pythinker's runtime tree
> against the project-wide rule "Prefer simplicity — no over-engineering"
> (`AGENTS.md` § Start). Each item below carries a concrete
> simplification, an alignment intent, an estimated LOC delta, a risk band, and
> a verification step. **No code is to be written from this document.** It is
> the input to a follow-up implementation plan that will land alongside
> this draft in `.agents/plans/` (maintainer-only — see §10).

---

## 0. Snapshot

| Surface | Files | LOC |
|---|---:|---:|
| `pythinker/` (runtime) | 163 | 46,837 |
| `tests/` | 246 | 58,541 |
| `webui/src/` | (TS/TSX) | 19,247 |
| `pythinker/agent/` | — | 12,493 |
| `pythinker/cli/` | — | 11,671 |
| `pythinker/channels/` | — | 7,550 |
| `pythinker/providers/` | — | 4,945 |

Top-10 fattest source files (descending):

| LOC | File | Notes |
|---:|---|---|
| 3,411 | `pythinker/cli/onboard.py` | 93 defs in one module |
| 3,240 | `pythinker/cli/commands.py` | 81 defs — CLI + REPL + mini-services |
| 2,008 | `pythinker/channels/websocket.py` | one fat class, 25 methods |
| 1,798 | `pythinker/agent/loop.py` | runtime spine; recently grew |
| 1,210 | `pythinker/channels/telegram.py` | single channel adapter |
| 1,130 | `pythinker/providers/openai_compat_provider.py` | shared OpenAI-compat impl |
| 1,116 | `pythinker/agent/runner.py` | `run()` orchestrator with ~25 nested helpers |
| 1,014 | `pythinker/agent/tools/pdf.py` | feature tool |
| 959 | `pythinker/agent/memory.py` | store + dream + consolidator |
| 912 | `pythinker/agent/tools/filesystem.py` | filesystem tools |

The runtime is **not bloated by file count** (163 files for ~47k LOC ≈ 287
LOC/file average) but is concentrated in a handful of monoliths. The top-10
files alone are **16,798 LOC** — 36% of the runtime tree. Simplification
targets that 36% first.

---

## 1. Guiding principles for this pass

These are the only rules an implementer should bring to the work below:

1. **Personal, single-user, localhost is the deployment.** Pythinker
   ships a token-checked gateway and the usual baseline guards (SSRF
   block-list, signed media URLs, bubblewrap sandbox layout, 0600 config
   perms, image MIME sniff). **Don't remove what already ships, and
   don't add new layers either.** No RBAC, no two-tier tokens, no Origin
   allowlists, no per-actor audit. If you find a guard added for a
   threat model the project doesn't support (e.g. the symlink/metadata
   shim recently dropped from `task_store.py`, commit `b8649e8`), drop
   it. Anything else — leave it where it is and move on.
2. **One responsibility per module.** A 3k-LOC file is almost always five
   modules pretending to be one. Splits do not move complexity around — they
   surface it for review.
3. **No drive-by refactors inside this plan.** Each phase touches one
   subsystem, has its own commit, and ships a green CI matrix.
4. **Tests follow code, but not in the split PR.**
   **Correction (Pass 4 → 5).** Earlier drafts said tests move with the
   code in the split PR. They don't — the split PR keeps existing import
   and monkeypatch paths working so behavior-preserving moves can be
   reviewed cleanly, and test relocation may happen in a follow-up
   cleanup PR after the compatibility shim has proven green.
5. **No new abstractions for hypothetical futures.** Every change in
   this plan must close a known smell, not open a future seam. If a
   second caller doesn't exist yet, the abstraction doesn't exist yet.
6. **Public Python API is wider than `pythinker/pythinker.py` +
   `pythinker/__init__.py`.** The third-party channel-plugin docs
   (`docs/channel-plugin-guide.md`) explicitly tell external authors to
   import:
   - `pythinker.channels.base.BaseChannel`
   - `pythinker.bus.events.OutboundMessage` (the guide's example only
     constructs `OutboundMessage`; `InboundMessage` is built implicitly
     via `BaseChannel.publish_inbound`, but it lives at the same path
     and downstream tools may still import it — treat the whole
     `pythinker.bus.events` module as load-bearing)
   - `pythinker.bus.queue.MessageBus`
   - `pythinker.config.schema.Base`
   - the `pythinker.channels` entry-point group
   These five symbols + the SDK facade are the **stable public surface**.
   Any move proposed below must keep these import paths working — split
   files freely, but leave a re-export at the original path. Concretely:
   `pythinker/channels/base.py`, `pythinker/bus/events.py`,
   `pythinker/bus/queue.py`, and `pythinker/config/schema.py` may be
   reorganized internally but must still expose the same symbol names from
   the same dotted paths.

---

## 2. File-by-file findings

Sections are ordered by *highest expected simplification yield first* so an
implementer who runs out of time still cuts the most weight per hour.

### 2.1 `pythinker/cli/onboard.py` — 3,411 LOC, 93 defs

**Smell.** The wizard is one linear file containing: banner/intro/outro
prose, provider picker, auth-method picker, OAuth flow plumbing, model
selector, workspace prompt, channel toggles, security disclaimer, plugin
discovery, summary screen, and the `OnboardResult`/`StepResult`/
`_WizardContext` types. Every step is a `_step_<name>(ctx) -> StepResult`
function. The state machine is implicit — readers must scan all 93 defs to
discover the order.

**Alignment intent.** Re-frame the wizard as a *list of step objects* (each
with `name`, `enter()`, optional `back()`), not 93 free functions sharing one
context bag. Steps then live in `pythinker/cli/onboard_steps/` with one file
per step (~50–150 LOC each). The driver in `onboard.py` shrinks to a
~150-LOC linear runner that owns the step list and the `_WizardContext`.

**Targets.**
- Split into one driver + ~15 step modules under `pythinker/cli/onboard_steps/`.
- Move `OnboardResult` / `StepResult` to `pythinker/cli/onboard_types.py`.
- Move provider/channel option-builder helpers (`_build_provider_options`,
  `_format_provider_hint`, `_provider_picker_bucket`,
  `_normalize_provider_id`, `_resolve_model_route_hint`,
  `_model_belongs_to_provider`) into `pythinker/cli/onboard_options.py`.
- Move OAuth helpers (`_login_via_oauth_remote`,
  `_set_provider_api_key`) into `pythinker/cli/onboard_auth.py`.

**Private-import compatibility.** Current tests and `tests/conftest.py`
reach directly into `pythinker.cli.onboard` for both public-ish types and
private wizard helpers. Run the §6 audit first — every name it surfaces
(across `from pythinker.cli.onboard import …`, `monkeypatch.setattr`, and
`patch("pythinker.cli.onboard.…")` callsites) must remain importable from
the old module. Notable categories observed today: result/context types
(`OnboardResult`, `StepResult`, `_WizardContext`), wizard machinery
(`_WIZARD_STEPS`, `_run_linear_wizard`, every `_step_*`), validators
(`_validate_field_constraint`, the `_SETTINGS_*` tables), interactive
shims (`_get_questionary`, `_BACK_PRESSED`, `WebSearchTool`), and patch
targets such as `get_config_path`, `save_config`, `run_onboard`,
`_login_via_oauth_remote`. Re-exporting a moved function is not enough
when tests patch a module global used by that function; either leave a
thin wrapper in `onboard.py` or make the moved helper resolve the
patched dependency through the compatibility module. The split PR is
not done until `pytest tests/cli tests/config tests/agent/test_onboard_logic.py`
is green without test edits.

**Estimated LOC delta:** −300 to −500 (inline prose dedup, banner removal,
collapsing the `StepResult` shape that exists only to carry a `next` enum).

**Risk:** Medium. Onboarding is exercised by `tests/cli/`, but the tests pin
many private import and patch paths on `pythinker.cli.onboard`.

**Verify.**
```
uv run pytest tests/cli -q
uv run pythinker onboard < /dev/null  # smoke runs the banner path
```

---

### 2.2 `pythinker/cli/commands.py` — 3,240 LOC, 81 defs

**Smell.** This file is six unrelated things glued together:

1. CLI entry point and `typer.Command` registration (~600 LOC).
2. Interactive REPL (`prompt_toolkit` integration, terminal restoration,
   spinners, ANSI rendering, paste handling) (~900 LOC).
3. Update banner / in-place upgrade plumbing (`_updates_enabled`,
   `_maybe_emit_update_banner`, `_run_in_place_upgrade`) (~250 LOC).
4. Provider/runtime construction (`_make_provider`, `_load_runtime_config`,
   `_load_browser_config`) (~400 LOC).
5. Server/gateway preflight (`_preflight_port_or_die`,
   `_get_websocket_channel`, `_webui_url_from_channel`,
   `_print_webui_startup_status`) (~200 LOC).
6. Interactive printing and progress-line UX (`_print_agent_response`,
   `_print_interactive_line`, `_response_renderable`, etc.) (~600 LOC).

Anything that is not "wire `argparse`/`typer` to a function" should leave.

**Alignment intent.** Treat `commands.py` as a *thin dispatch shim* and move
each of the six clusters out:

| Cluster | Lands at |
|---|---|
| Interactive REPL | `pythinker/cli/repl.py` |
| Update banner / upgrade | `pythinker/cli/updates.py` |
| Runtime construction | `pythinker/cli/bootstrap.py` |
| Server preflight + WebUI URL | `pythinker/cli/serve.py` |
| Interactive printing | `pythinker/cli/render.py` |
| Entry point + Typer wiring | stays in `commands.py` (~500 LOC) |

**Estimated LOC delta:** −400 (de-duplication of three near-identical
"render markdown or fall back to plain" branches; collapse two
`_print_*_progress_line` variants into one).

**Risk:** Medium. `commands.py` is the user-facing CLI; touching the REPL
risks subtle terminal-mode regressions (the `SafeFileHistory` shim and
`_restore_terminal` exist for a reason — the original incident is in git
log).

**Verify.**
```
uv run pytest tests/cli -q
echo "/help" | uv run pythinker agent     # REPL smoke
uv run pythinker --help
uv run pythinker doctor                    # full diagnostic
```

---

### 2.3 `pythinker/channels/websocket.py` — 2,008 LOC, 25 methods on one class

**Smell.** `WebSocketChannel` is responsible for: token issuance & validation
HMAC, WebSocket multiplex protocol (control envelopes + chat messages), HTTP
REST surface (sessions list, bootstrap, signed media URLs, admin proxy,
config snapshot), file upload handling, image MIME sniffing (off-thread
bytes inspection), retry/backoff, and the `WebSocketConfig` model. 25
methods on one class.

**Alignment intent.** Carve out by surface, not by hand. Three peer modules
in `pythinker/channels/websocket/`:

```
pythinker/channels/websocket/
├── __init__.py        # re-exports WebSocketChannel + WebSocketConfig (back-compat)
├── channel.py         # the BaseChannel subclass (lifecycle, bus wiring) ~400 LOC
├── auth.py            # token issuance, HMAC, single-use media URLs ~250 LOC
├── multiplex.py       # WS message routing + control envelopes ~400 LOC
├── rest.py            # aiohttp REST handlers ~500 LOC
├── media.py           # MIME sniff, image upload validation ~150 LOC
└── config.py          # WebSocketConfig + Pydantic shape ~100 LOC
```

The directory layout then matches `pythinker/agent/browser/`, which already
took this approach for browser session management.

**Private-import compatibility.** The websocket tests reach past the
`WebSocketChannel` / `WebSocketConfig` re-exports for unit-level
helpers. Before splitting, grep `tests/channels/` for any of:

- `_parse_*`, `_b64url_*` (token / signed-URL helpers)
- `_is_local_bind` (bind-address check)
- `_extract_data_url_mime` (data-URL parser)
- `get_media_dir` (media path resolver)

…and add a re-export in the new `pythinker/channels/websocket/__init__.py`
for every name that tests still import or `monkeypatch`. Sample
call-sites at the time of writing: `tests/channels/test_websocket_channel.py:17`,
`tests/channels/test_websocket_media_route.py:24`,
`tests/channels/test_websocket_envelope_media.py:19`. The PR is not
done until `pytest tests/channels` is green without test edits.

**Estimated LOC delta:** −250 (drop duplicate "build session bundle" code
between bootstrap REST and message-history WS, collapse two near-identical
"emit signed media URL" helpers).

**Risk:** Medium-high — this is the WebUI's only transport. Mitigated by
strong test coverage in `tests/channels/test_websocket*.py` and the
recently-landed admin live-control tests.

**Verify.**
```
uv run pytest tests/channels -q
cd webui && bun run test
uv run pythinker gateway &  # smoke; hit http://127.0.0.1:18790/api/auth/...
```

---

### 2.4 `pythinker/agent/loop.py` — 1,798 LOC

**Smell.** `AgentLoop` carries the runtime spine but has accreted: provider
hot-reload (`_apply_provider_snapshot`, `_refresh_provider_snapshot`),
browser hot-reload (`_browser_storage_dir`, `_register_browser_tool`), tool
registration (`_register_default_tools`), context normalization for
*four* call sites (`_normalize_context`, `_normalize_context_for_direct`,
`_normalize_context_for_cron`, `_normalize_context_for_heartbeat`),
think-block stripping (`_strip_think`), tool-hint generation (`_tool_hint`),
agent identity resolution (`_resolve_agent`), and the per-session
checkpoint/pending-queue plumbing.

The file is held together by `AgentLoop.__init__`, which takes ~30
parameters. That signature alone is the loudest possible "this object is
doing too much."

**Alignment intent.**

1. **Tool registration → `pythinker/agent/loop_tools.py`.** Pure function
   `register_default_tools(registry, *, workspace, exec_config, web_config,
   browser_config, restrict_to_workspace, ...)`. Removes ~250 LOC and the
   duplicated browser-storage logic.
2. **Hot-reload → `pythinker/agent/loop_reload.py`.** The provider/browser
   refresh dance is mechanical and self-contained — split it.
3. **Context normalization → `pythinker/agent/loop_context.py`.** The
   shared `_normalize_context` helper already exists at
   `pythinker/agent/loop.py:746`, with thin per-call-site wrappers
   (`_normalize_context_for_direct`, `_normalize_context_for_cron`,
   `_normalize_context_for_heartbeat`) at lines 764, 777, 785. The work
   here is purely a *file move* — lift the 4-function cluster to its
   own module so `loop.py` reads as "lifecycle + checkpoint + run"
   without the context-shape plumbing. No deduplication needed.
4. **`_clamp_context_window` → `pythinker/providers/limits.py`.** Already
   pure-functional; doesn't belong in the loop file.

After the carve-out, `loop.py` lands around 1,100 LOC — still big, but
now actually about *the loop*. **`AgentLoop.__init__` does not shrink**
on its own from these moves — `tool_registration` lifting changes who
*uses* the constructor inputs, not which inputs the constructor takes.
The current 27-param signature (`pythinker/agent/loop.py:219`) stays
roughly the same after this phase. Collapsing the signature would
require a config-object redesign, which conflicts with §1 principle 5
("no new abstractions") and is therefore explicitly out of scope.

**Estimated LOC delta:** −400 across moves; net file count +3.

**Risk:** Medium. The mid-turn checkpoint plumbing (`_RUNTIME_CHECKPOINT_KEY`
/ `_PENDING_USER_TURN_KEY`) is touchy — `CLAUDE.md` explicitly warns that
renaming either breaks live sessions. **Do not move those constants.**

**Verify.**
```
uv run pytest tests/agent -q
uv run pytest tests/runtime -q   # policy/egress
uv run pytest tests/session -q
```

---

### 2.5 `pythinker/agent/runner.py` — 1,116 LOC

**Correction.** Earlier draft said `AgentRunner.execute`. The public
entry point is `AgentRunner.run` at `pythinker/agent/runner.py:277`;
`execute` at line 57 is an internal step. Section now refers to `run`.

**Smell.** `AgentRunner.run` is the multi-turn driver: build messages →
call provider → handle response shape A (Chat-Completions) → handle
response shape B (Anthropic / Responses API) → dispatch tool calls →
spool tool results → fold subagent injections → emit budget telemetry →
checkpoint. The method has accreted ~25 nested helpers in its body.

**Alignment intent.** Extract the named phases into private methods on
`AgentRunner`. Names are illustrative — adopt whatever the code already
calls them when reading the actual body:

- `_build_request_messages()`
- `_call_provider_with_retries()`
- `_dispatch_tool_calls()`
- `_apply_subagent_injections()`
- `_emit_telemetry()`
- `_persist_checkpoint()`

`run()` becomes a 60-line orchestrator that calls them in order. No
behavior change.

**Estimated LOC delta:** ~0 (a refactor that is purely about readability —
the win is in mental load, not LOC).

**Risk:** Medium. The runner is the only place where Anthropic and OpenAI
response-shape divergence is reconciled; a botched split risks breaking one
provider family silently.

**Verify.**
```
uv run pytest tests/agent/test_runner.py -q
uv run pytest tests/providers -q
```

---

### 2.6 `pythinker/providers/openai_compat_provider.py` — 1,130 LOC

**Smell.** This file holds *both* the Chat-Completions code path and the
Responses-API code path, plus the circuit-breaker that flips between them
(`_RESPONSES_FAILURE_THRESHOLD`, `_RESPONSES_PROBE_INTERVAL_S`,
`_record_responses_failure`, `_record_responses_success`, plus
`_should_use_responses_api`, `_should_fallback_from_responses_error`,
`_build_responses_body`). Two API shapes living in one class.

The Responses API surface is only used by a subset of providers (newer
OpenAI / Azure / Codex). Most providers take the Chat-Completions path.

**Alignment intent.**

1. **Move the Responses-API logic to `pythinker/providers/openai_responses/`**
   (the directory already exists; `parsing.py` and `converters.py` are in
   place, exported by `pythinker/providers/openai_responses/__init__.py`
   and used by `tests/providers/test_openai_responses.py`). Land
   `chat.py` for the Responses-API call shape and `circuit.py` for the
   failure/probe state. **Do not rename `parsing.py` to `parse.py`** —
   that would break the existing import.
2. **`OpenAICompatProvider` keeps only the Chat-Completions path** plus a
   small `_responses_handler` attribute that points to the Responses-API
   adapter. Provider specs that don't use Responses get a `None` handler and
   the dispatcher just calls Chat-Completions.
3. **Tool-call ID normalization** (`_normalize_tool_call_id`,
   `_normalize_tool_call_arguments`, `_sanitize_messages`) → shared utility
   `pythinker/providers/_message_sanitize.py` (already used implicitly by
   the Anthropic provider's role-alternation logic; same code, two homes).

**Estimated LOC delta:** −150 (de-duplicate sanitize logic + drop the
"is this provider on the Responses path" branches that no longer apply
once the Responses code is its own object).

**Risk:** High. This is the single most-exercised code path in the
provider tree; the circuit breaker has shipped specifically to handle
real-world failures. Implementer must keep the existing
`_RESPONSES_FAILURE_THRESHOLD=3` / `_RESPONSES_PROBE_INTERVAL_S=300`
constants and their behavior.

**Verify.**
```
uv run pytest tests/providers -q
uv run pytest tests/providers/test_openai_responses.py tests/providers/test_responses_circuit_breaker.py -q
```

---

### 2.7 `pythinker/agent/memory.py` — 959 LOC

**Smell.** Holds three peers: `MemoryStore` (file I/O), `Consolidator`
(token-budget-bounded summarizer), and `Dream` (scheduled two-phase agent
that auto-commits memory edits via `dulwich`). They share imports but very
little code.

**Alignment intent.** Three files:

```
pythinker/agent/memory/
├── __init__.py        # re-exports MemoryStore, Consolidator, Dream + compatibility helpers
├── store.py           # MemoryStore + path conventions ~250 LOC
├── consolidator.py    # Consolidator ~300 LOC
└── dream.py           # Dream + dulwich integration ~350 LOC
```

**Private-import compatibility.** Tests currently monkeypatch
`pythinker.agent.memory.estimate_message_tokens` through a module alias while
exercising `Consolidator`. If `Consolidator` moves to
`memory/consolidator.py` and imports `estimate_message_tokens` directly from
`pythinker.utils.helpers`, that monkeypatch stops affecting the code under
test. Keep `estimate_message_tokens` exported from `pythinker.agent.memory`
and either have the consolidator resolve token estimation through the
compatibility module or adjust the split so the existing monkeypatch target
remains authoritative. Do not edit those tests in the split PR.

**Estimated LOC delta:** −60 (drop a near-duplicate "render system prompt
for memory edit" between `Dream` and `MemoryStore.summarize`).

**Risk:** Low. `dulwich` integration is contained; tests in
`tests/agent/test_dream.py` and `tests/agent/test_consolidator.py` pin
behavior.

**Verify.**
```
uv run pytest tests/agent/test_dream.py tests/agent/test_consolidator.py tests/agent/test_memory_store.py -q
```

---

### 2.8 `pythinker/channels/telegram.py` — 1,210 LOC

**Smell.** A "thin" channel adapter should be ~300 LOC. Telegram is fat
because it owns: webhook vs. polling switch, markdown→HTML conversion
(`_markdown_to_telegram_html` is 200+ LOC of regex pipeline), media
upload/download, voice-note transcription glue, command parsing, error
handler, **and** Telegram-specific keyboard/inline-button rendering.

**Alignment intent.**

1. Extract `_markdown_to_telegram_html` (and its sister helpers) to
   `pythinker/channels/telegram_markdown.py`. It is already imported from
   `tests/command/test_task_commands.py` for backtick assertions — that
   import will move with it.
2. Extract media-handling helpers to `pythinker/channels/telegram_media.py`.
3. The remaining `TelegramChannel` class lands around 500 LOC.

**Estimated LOC delta:** ~0 (refactor for readability).

**Risk:** Medium. The markdown converter is the only thing keeping Telegram
output legible — any regression is immediately user-visible.

**Verify.**
```
uv run pytest tests/channels/test_telegram*.py -q
```

---

### 2.9 `pythinker/agent/tools/pdf.py` — 1,014 LOC

**Correction.** An earlier draft of this plan claimed this file did both
render and extract. It does not — it is generation-only (`MakePdfTool` is
a Markdown → styled PDF report renderer; the file's own header docstring
calls it "PDF report generation tool"). PDF text extraction lives in
`pythinker/utils/document.py::_extract_pdf`, a separate small helper.

**Smell.** A single 1,000-LOC file is still large for one tool, but the
size is mostly inline CSS/HTML template, font metrics, and the
Markdown-subset parser. Each of those pieces is genuinely cohesive. There
is no clean two-way split here.

**Alignment intent.** Drop the previous "split render vs. extract" idea.
The honest options are:

1. **Leave it.** Acceptable. The file is internally coherent.
2. **Move the inline CSS template + the font metric tables** to
   `pythinker/agent/tools/pdf_assets.py` so the Python logic and the
   styling assets are physically separate. This is a 200–300 LOC move
   that genuinely reduces cognitive load when reading the tool.

Recommend option 2 only if Phase A or D ends up shorter than expected and
there is appetite for one more polish PR. Otherwise leave the file as it
is.

**Estimated LOC delta:** 0 (option 1) or ~0 (option 2 — pure file move).

**Risk:** Low.

**Verify.**
```
uv run pytest tests/agent/tools/test_pdf*.py -q
```

---

### 2.10 `pythinker/agent/tools/filesystem.py` — 912 LOC

**Smell.** Four tools (`ReadFileTool`, `WriteFileTool`, `EditFileTool`,
`ListDirTool`) plus the workspace-restriction guard logic shared between
them. The shared guard accumulated retry/backoff for atomic writes,
encoding sniff, and binary-detection — all reasonable but mashed in.

**Alignment intent.**

1. Move the shared guard/normalization helpers to
   `pythinker/agent/tools/filesystem_guard.py` (workspace check, path
   resolution, binary sniff, atomic write).
2. Each tool stays in `filesystem.py` but each becomes ~150 LOC because
   the shared guard is gone.
3. Drop `file_state.py` if it's unused (verify first; it appears to track
   read-before-edit gating used by `EditFileTool`, in which case it stays).

**Estimated LOC delta:** −80 (de-duplication of three near-identical
"resolve and validate path" flows).

**Risk:** Low.

**Verify.**
```
uv run pytest tests/agent/tools -q
```

---

### 2.11 `pythinker/admin/service.py` — 724 LOC

**Smell.** Long file of "build a JSON snapshot of `Config` for the admin
dashboard" helpers (`_provider_routing`, `_provider_rows`,
`_required_secret_status`, `_tools_surface`, `_runtime_surface`,
`_redact_url`, `_backup_to_payload`). Sandwiched between is the actual
`AdminService` class and its mutation routes (stop/restart/cancel that
landed in commit `5a77b68`).

**Alignment intent.**

1. Move the snapshot helpers to `pythinker/admin/snapshot.py` (one pure
   function per top-level dashboard section).
2. `AdminService` keeps the mutation routes and the surface composition.
3. Move the redaction helpers (`_redact_url`, `_redacted_path_is_set`,
   `_path_value`) to `pythinker/admin/redact.py` — they're testable in
   isolation. The current admin test surface is thin
   (`tests/admin/test_config_backups.py` only), so the split PR should add
   focused tests for snapshot/redaction behavior under `tests/admin/`.

**Estimated LOC delta:** −60.

**Risk:** Low.

**Verify.**
```
uv run pytest tests/admin -q          # currently only test_config_backups.py
cd webui && bun run test admin
```
*Note: the admin Python test surface is currently thin (one file). New
tests for the snapshot/redact split should be added with the PR.*

---

### 2.12 `pythinker/command/builtin.py` — 695 LOC

**Smell.** All ~20 chat slash-commands live in one file: `cmd_help`,
`cmd_status`, `cmd_dream*`, `cmd_new`, `cmd_restart`, `cmd_stop`,
`cmd_tasks`, `cmd_task_output`, `cmd_task_stop`, plus the formatters
(`_format_task_row`, `_escape_markdown_text`, `_fenced_text`) and helper
predicates (`_task_record_for_session`,
`_task_output_record_for_session`, `_task_id_from_args`).

The recent task-spine PR (`db2ed10`, "Feature/autonomous task spine")
added ~100 LOC of slash-command surface to this file. If follow-on
work lands more `/task-*` commands, the file will keep growing in this
direction — group by topic *now* rather than later.

**Alignment intent.** Group commands by topic into modules under
`pythinker/command/builtins/`:

```
pythinker/command/builtins/
├── __init__.py     # imports/registers grouped handlers
├── lifecycle.py    # /new, /restart, /stop, /status, /help
├── dream.py        # /dream, /dream-log, /dream-restore
├── tasks.py        # /tasks, /task-output, /task-stop
└── format.py       # _escape_markdown_text, _fenced_text, _format_task_row
pythinker/command/builtin.py  # legacy shim; keeps old dotted path alive
```

**Import/patch compatibility.** `pythinker.command.builtin` is the current
load-bearing import path for runtime registration, channel help rendering, and
tests. Keep `pythinker/command/builtin.py` as a compatibility module that
exports every old handler/helper (`cmd_*`, `build_help_text`,
`register_builtin_commands`, formatting helpers used by tests). Existing tests
also patch `pythinker.command.builtin.asyncio` and
`pythinker.command.builtin.os.execv` around `/restart`; a plain re-export of
`cmd_restart` from `builtins/lifecycle.py` will not make those patches affect
the moved function's globals. Either keep a patchable wrapper for `cmd_restart`
in `builtin.py`, or make the implementation read those dependencies through
the compatibility module. The split PR is not done until `pytest tests/command
tests/cli/test_restart_command.py` is green without test edits.

**Estimated LOC delta:** ~0 (refactor for navigability).

**Risk:** Low.

**Verify.**
```
uv run pytest tests/command -q
```

---

### 2.13 Other source files worth a smaller pass

- `pythinker/session/manager.py` (668) — split persistence (jsonl writes,
  TTL eviction) from the `SessionManager` API surface.
- `pythinker/agent/tools/mcp.py` (625) — already self-contained; only
  worth a polish pass to remove `# TODO: revisit` comments.
- `pythinker/providers/anthropic_provider.py` (601) — extract the
  cache-control marker logic (Anthropic-only) to a peer module.
- `pythinker/cli/tui/app.py` (749) — split per-screen widgets out of
  `app.py` into a `screens/` directory.
- `pythinker/cli/doctor.py` (521) — already linear; one polish pass to
  drop dead `_v1` checks if any.

These are *opportunistic* — only fold them in if they sit naturally inside
a larger phase from §3.

---

## 3. Phasing

The unit of shipping is the **work-item PR**, not the phase. Each
numbered work item below is one PR with its own green CI. A phase is
just a grouping for sequencing — Phase A is "the cheap moves" and
contains 4–5 PRs, not one super-PR.

Work items are independent **except where §13's cross-cutting items
intersect**: X3 (`_clamp_context_window` extraction) is also part of
B2; if X3 ships first, B2 imports the new home; if B2 ships first, X3
collapses to a no-op. No other work-item dependencies exist.

### Phase A — Cheap & big yield (recommended first)

1. `pythinker/cli/onboard.py` split (§2.1)
2. `pythinker/agent/memory.py` split (§2.7)
3. `pythinker/agent/tools/pdf.py` asset extraction — *optional polish only*
   (§2.9, after correction)
4. `pythinker/admin/service.py` split (§2.11)
5. `pythinker/command/builtin.py` regroup (§2.12)

**Why first:** Each is a near-pure file move with low risk, low review
surface, and tests that already exercise the module's public face. None
touch the runtime spine.

**Expected delta:** −350 to −600 LOC *deleted* (the rest of any LOC
movement is pure code-move into new files; tracked separately in §8),
+9 to +12 files, 4–5 work-item PRs (A3 may ship as a 0-PR no-op).

### Phase B — Runtime spine clarity

6. `pythinker/agent/runner.py` phase split (§2.5)
7. `pythinker/agent/loop.py` carve-out (§2.4)

**Why second:** Both are central to every turn. Run after Phase A so the
reviewer has a clean baseline; CI matrix confidence is highest right after
a docs-only week.

**Expected delta:** −400 LOC *deleted*, +5 files, 2 work-item PRs.

### Phase C — Provider tree

8. `pythinker/providers/openai_compat_provider.py` split (§2.6)

**Why last:** Highest blast radius. Implementer should keep a Responses-API
test fixture plus a Chat-Completions fixture and run both before PR.

**Expected delta:** −150 LOC *deleted*, +3 files, 1 work-item PR.

### Phase D — Channels & tools polish (opportunistic)

9. `pythinker/channels/websocket.py` directory split (§2.3)
10. `pythinker/channels/telegram.py` markdown extraction (§2.8)
11. `pythinker/agent/tools/filesystem.py` guard extraction (§2.10)

**Why last:** WebSocket split is the riskiest; do it only after the runtime
spine is calm.

**Expected delta:** −300 LOC *deleted*, +9 files, 3 work-item PRs.

### Phase E — `pythinker/cli/commands.py` decomposition (§2.2)

12. The full six-cluster split.

**Why last of all:** This file is the user's entry point. Mistakes here
break `pythinker --help`, `pythinker agent`, `pythinker gateway`, and the
auto-update banner. Land Phases A–D first so the surrounding code is in its
final shape and the diff for this phase is purely about CLI plumbing.

**Cross-module callers to keep working.**
`pythinker/cli/tui/app.py:276` does
`from pythinker.cli.commands import _load_browser_config,
_load_runtime_config, _make_provider`. When these three move to
`pythinker/cli/bootstrap.py`, **either** keep re-exports in
`pythinker/cli/commands.py` **or** update the TUI import in the same
PR. Don't ship the move without doing one of the two.

**Expected delta:** −400 LOC *deleted*, +5 files, 1 work-item PR.

---

## 4. Cross-cutting cleanups

These are smaller than a full phase but worth a single PR each.

### 4.1 ~~Drop `pythinker/templates/`~~ — withdrawn

An earlier draft of this plan listed `pythinker/templates/` as dead code
because its `__init__.py` files are empty. **That recommendation is wrong
and is hereby withdrawn.** The directory holds the runtime Jinja2 prompt
templates (`agent/identity.md`, `agent/platform_policy.md`,
`agent/_snippets/*.md`, etc.) loaded by
`pythinker/utils/prompt_templates.py:14` via
`_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"`.
Removing it would break the agent's system-prompt rendering at startup.

The empty `__init__.py` files exist purely so the directory ships in the
wheel (Hatch's `force-include` doesn't need them, but they're harmless).
Leave the directory as-is.

### 4.2 ~~`PolicyDecision.allowed` ↔ `behavior` reconciliation~~ — withdrawn

An earlier draft said the `PolicyDecision` dataclass carried both
`allowed` and `behavior` and needed reconciliation. **That is wrong.** The
current shape (`pythinker/runtime/policy.py:28`) is exactly:

```python
@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
```

No `behavior` field exists. An earlier internal design note proposed a
`behavior` literal as Phase-3 future work for an approval-aware policy,
but Phase 3 has not landed, the design note is not committed to this
worktree, and the field is not in the code. There is nothing to
reconcile.

Recommendation: **leave `PolicyDecision` alone** in this initiative. If
the Phase-3 approval-aware policy ever lands, that PR is responsible for
its own field migration.

### 4.3 Consolidate context-window clamp

`_clamp_context_window` in `loop.py` is one of three places that already
peek at provider input caps. Move it to
`pythinker/providers/limits.py` and have the loop import from there
(also lets future SDK callers reuse it without importing the loop).

### 4.4 Reconcile `_make_provider` between CLI and SDK facade

**Correction.** An earlier draft framed this as "API ↔ CLI
consolidation" and was withdrawn after `pythinker/api/server.py` turned
out to take a pre-built `AgentLoop`. But there are still **two**
`_make_provider` definitions in the tree:

- `pythinker/cli/commands.py:698` — CLI-facing wrapper that adds
  Rich-formatted error printing + `typer.Exit(1)` on validation failure.
- `pythinker/pythinker.py:159` — SDK-facade wrapper that just
  re-raises. Covered by `tests/test_pythinker_facade.py:136`.

Both delegate to `pythinker/providers/factory.make_provider`, so the
duplication is thin. **Phase E action item:** when the CLI helper
moves to `pythinker/cli/bootstrap.py`, *also* check that the SDK copy
hasn't drifted — both should call `providers.factory.make_provider`
and differ only in error-reporting style. Don't unify them into one
function (the SDK must not import `typer`/`rich`); just verify the
delegation contract still holds.

### 4.5 ~~Skill validator shouldn't be a runtime file~~ — withdrawn

The validator at `pythinker/skills/skill-creator/scripts/quick_validate.py`
ships in the wheel because it's a member of the `pythinker/` package,
not via `[tool.hatch.build.targets.wheel.force-include]` (which
currently lists only the WhatsApp `bridge/` directory). The relevant
lever to *exclude* it would be `[tool.hatch.build.targets.wheel.exclude]`
or moving the script outside the package — neither is in scope for a
simplification plan. Workspaces also call this validator directly
(`tests/test_agents_skills.py` re-uses it). Leave as-is.

---

## 5. Out-of-scope for this plan

The following are explicit **non-goals** so the implementer doesn't expand
scope:

- **Frontend (`webui/`).** `AdminDashboard.tsx` (853), `ConfigWorkbench.tsx`
  (742), and `ThreadComposer.tsx` (705) are above their natural size, but
  React component splits are a different conversation (state colocation,
  hook extraction, Radix prop forwarding). Defer to a separate plan.
- **Test reorganization.** `tests/` is 58k LOC vs. 47k source LOC. That's
  a normal ratio for a project this provider-heavy and channel-heavy; we
  don't need to thin tests.
- **WhatsApp bridge (`bridge/`).** Out of the Python tree. Untouched.
- **`pythinker/skills/skill-creator/scripts/init_skill.py`.** All eight
  TODOs in the codebase live in template strings inside this scaffolding
  generator; they are intentional.
- **Future task-spine phases** (control envelopes, approval broker).
  Those are *additions*, not simplifications. If/when they land, they
  bring their own plan.
- **`pythinker/__init__.py`.** Public SDK surface. Hands off until 1.0.

---

## 6. Verification policy

Every phase MUST close with green:

```bash
uv run ruff check pythinker --select F401,F841   # CI's strictest gate
uv run pytest <phase-specific subset>            # (see each section)
uv run pytest                                    # full suite, before PR
```

**Import/patch compatibility checklist (every file→package split).**
Before flipping a `foo.py` module to a `foo/` package, the implementer
MUST:

1. `grep -rn "from pythinker.<dotted.path> import" tests/` to enumerate
   every name imported off the old module — *including private
   underscore names*.
2. `grep -rn "monkeypatch.*pythinker.<dotted.path>" tests/` and
   `grep -rn "patch.*pythinker.<dotted.path>" tests/` to enumerate
   every name `monkeypatch` / `unittest.mock.patch` reaches into.
3. Re-export every hit in the new `__init__.py`. The split is not done
   until `pytest <subset>` is green *without editing the test files*.
   For patch targets, a re-export may be insufficient because a moved
   function keeps the globals of its new module; keep wrappers or dependency
   lookups at the old dotted path when tests patch that path. Test edits are
   allowed in a follow-up PR; the split itself must be a pure code-move.

For phases that touch `pythinker/channels/` or `pythinker/cli/`:

```bash
uv run pythinker doctor      # smoke
echo "/help\nexit" | uv run pythinker agent
```

For phases that touch `pythinker/providers/`:

```bash
uv run pytest tests/providers -q
# Plus one live request per provider family the maintainer has keys for.
```

CI matrix is `{ubuntu, windows} × {3.11, 3.12, 3.13, 3.14}`. Windows is
where the recent task-spine PR's cross-platform bug surfaced (PR #2,
`0a1da5f` — CRLF text-mode + clock-resolution sort tie); every PR from
this plan should explicitly review:

- **Path/IO:** Is anything new doing text-mode write of bytes the reader
  consumes via `"rb"`? If yes, switch to binary.
- **Sort keys:** Does any new sort lean on a wall-clock string for tie
  ordering? If yes, add an insertion-order tiebreaker.

---

## 7. What this plan is NOT

It is not a plan to:

- Add features.
- Change the SDK surface (`pythinker/pythinker.py`,
  `pythinker/__init__.py`).
- Touch the WhatsApp bridge.
- Reorganize tests beyond moving them next to the source they cover.
- Introduce new abstractions (no `Service`/`Manager`/`Strategy`/`Registry`
  shells — the existing ones are enough).
- Add type-coercion helpers (the recently-removed `_metadata_str` /
  `_metadata_optional_str` / `_metadata_int` / `_metadata_dict_list`
  cluster from `task_store.py` is the cautionary tale).

If a future PR reaches for any of those, route it through a separate plan.

---

## 8. Estimated total impact

Two metrics, tracked separately because they answer different
questions:

**(a) Net deletion** — code that goes away entirely (de-duplicated
helpers, collapsed branches, dropped fallbacks):

| Metric | Before | After (projected) | Delta |
|---|---:|---:|---:|
| Runtime LOC | 46,837 | ~45,000 | **−1,800** |
| Files >1,000 LOC | 8 | ~2 | **−6** |

**(b) Top-heavy concentration** — LOC remaining in the ten fattest
files. Most of this drop is pure file-move out of monoliths into peer
modules; the LOC still exists, just in narrower files:

| Metric | Before | After (projected) | Delta |
|---|---:|---:|---:|
| LOC in top-10 | 16,798 (36%) | ~11,000 (24%) | **−5,800** |

**Not a metric:** `AgentLoop.__init__` parameter count. Earlier draft
claimed a drop from ~30 → ~10 from these moves; that's wrong (see §2.4
Correction). Constructor shape is unchanged.

Net new files: ~30 small modules. The repo grows in *file count* but
shrinks in per-file *cognitive load*, which is the actual goal.

---

## 9. Open questions for the maintainer

1. **Phase ordering preference?** This plan suggests A → B → C → D → E.
   An equally reasonable order is A → E → B → D → C if you'd rather de-risk
   the CLI surface earliest.
2. **Worktree per phase?** Land sequentially on `dev` by default. Use
   a git worktree (`git worktree add ../pythinker-<phase> dev`) only if
   the maintainer wants to keep `dev` building while a long-running
   carve is in progress. No project-wide convention is required either
   way.
3. **PR cadence cap and branch target?** `CONTRIBUTING.md:30,66` says
   refactors target `dev`, with cherry-pick to `main` for stable
   features. Default plan: every phase here is a refactor → all PRs
   target `dev`. Maintainer can override per phase if a particular
   move is bug-fix-shaped (e.g. closing a documented inconsistency).
4. **Public SDK callers.** Per §1 principle 6 the stable surface is the
   six imports documented in `docs/channel-plugin-guide.md`:
   `pythinker.channels.base.BaseChannel`,
   `pythinker.bus.events.{InboundMessage,OutboundMessage}`,
   `pythinker.bus.queue.MessageBus`, `pythinker.config.schema.Base`,
   the `pythinker.channels` entry-point group, and the SDK facade
   (`pythinker.pythinker.Pythinker` + `pythinker.__init__`). Any phase
   that touches `pythinker/channels/base.py`, `pythinker/bus/`, or
   `pythinker/config/schema.py` MUST keep these dotted import paths
   working — feel free to split files internally, but leave a
   re-export at the original path.

---

## 10. Approval gate

This document is a *draft plan*. It must not be turned into commits until:

1. The maintainer confirms phase order and PR-target branch.
2. The maintainer confirms questions in §9.
3. A separate "Implementation Plan" document is authored under
   `.agents/plans/<date>-simplification-phase-<N>-impl.md`. (Both the
   draft plan and the impl plan are maintainer-only; they live under
   `.agents/` so `.agents/README.md`'s "don't reference from public
   docs" rule applies uniformly. The earlier draft pointed at
   `docs/superpowers/plans/`, which would have leaked maintainer
   material into the public docs tree.)

Implementation plans take this draft as their input — they are *not* this
document.

---

## 11. Comprehensive per-file inventory

Every Python file in `pythinker/` is listed below with a finding. No file
is skipped. Annotations:

- **CARVE** — covered by a phase in §3; cross-reference is given.
- **POLISH** — small, in-place cleanup worth a single PR.
- **PASS** — file is at its right size and shape; do not touch.
- **DEAD?** — looks unreferenced; verify before removing.
- **API** — public surface; do not modify in this initiative.

Counts in parentheses are LOC.

### 11.1 Top-level package

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 36 | `pythinker/__init__.py` | **API** | Public SDK entrypoint. Hands off. |
| 13 | `pythinker/__main__.py` | **PASS** | Three-line `python -m pythinker` shim. |
| 169 | `pythinker/pythinker.py` | **API** | Public SDK facade. Hands off. |

### 11.2 `pythinker/admin/` (729 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 5 | `__init__.py` | **PASS** | Re-exports. |
| 724 | `service.py` | **CARVE** §2.11 | Snapshot helpers + redaction split out. |

### 11.3 `pythinker/agent/` (12,493 LOC) — runtime spine

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 20 | `__init__.py` | **PASS** | Re-exports. |
| 1,798 | `loop.py` | **CARVE** §2.4 | Tool registration / hot-reload / context-norm split. |
| 1,116 | `runner.py` | **CARVE** §2.5 | `run()` orchestrator phase-extract. |
| 959 | `memory.py` | **CARVE** §2.7 | Triple split: store/consolidator/dream. |
| 534 | `subagent.py` | **POLISH** | Recently simplified (PR #1). One pass to flatten the four `_announce_result` branches into one helper that takes status/output. |
| 290 | `task_store.py` | **PASS** | Just simplified (PR #1, #2). Don't touch. |
| 242 | `skills.py` | **PASS** | Skills loader; small and focused. |
| 209 | `context.py` | **PASS** | Pure context-builder. Right size. |
| 139 | `search.py` | **PASS** | Local-only history search; good shape. |
| 124 | `autocompact.py` | **PASS** | TTL-based compactor; tight. |
| 121 | `hook.py` | **PASS** | `AgentHook` ABC + context dataclass. |
| 115 | `usage_ledger.py` | **PASS** | Append-only token-usage ledger. Imported by `loop.py:961`. Right size. |
| 78 | `chat_title.py` | **PASS** | Title-generation helper. |
| 60 | `tasks.py` | **PASS** | Just simplified. Don't touch. |
| 44 | `usage.py` | **PASS** | Live — exposes `estimate_session_usage`, imported by `pythinker/channels/websocket.py:986` and `pythinker/admin/service.py:15`. Distinct surface from `usage_ledger.py` (cheap on-demand estimator vs. durable ledger). |

#### 11.3.1 `pythinker/agent/browser/` (547 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 0 | `__init__.py` | **PASS** | Empty re-export. |
| 399 | `manager.py` | **PASS** | Already-split structure; mirror this layout for the websocket carve in §2.3. |
| 121 | `state.py` | **PASS** | Browser-state dataclasses. |
| 27 | `transport.py` | **PASS** | Tiny CDP transport adapter. |

#### 11.3.2 `pythinker/agent/tools/` (4,983 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 27 | `__init__.py` | **PASS** | Re-exports. |
| 1,014 | `pdf.py` | **POLISH** §2.9 | Render-only (`MakePdfTool`). Optional asset/template extraction; previous "render vs extract" split was incorrect. |
| 912 | `filesystem.py` | **CARVE** §2.10 | Guard helpers split. |
| 625 | `mcp.py` | **POLISH** | Self-contained; only worth a TODO sweep and a peek at whether the unused `# TODO: revisit` lines can be deleted (dev's words). |
| 555 | `search.py` | **POLISH** | Two grep/glob tools + ripgrep fallback shim. Look for the third near-identical "render result row" branch and collapse. |
| 449 | `self.py` | **PASS** | The `my` tool — already focused. |
| 443 | `web.py` | **PASS** | `web_search` + `web_fetch`; thin glue around providers. |
| 318 | `shell.py` | **PASS** | Bubblewrap-wrapped exec; security-critical, leave as-is. |
| 301 | `browser.py` | **PASS** | Browser-tool dispatcher; coupled to manager. |
| 279 | `base.py` | **PASS** | Tool ABC + concurrency metadata. |
| 278 | `cron.py` | **PASS** | Cron tool surface; tight. |
| 232 | `schema.py` | **PASS** | Tool schema fragments; declarative. |
| 161 | `notebook.py` | **PASS** | `notebook_edit` tool; small. |
| 127 | `message.py` | **PASS** | `message` tool; small. |
| 125 | `registry.py` | **PASS** | Tool registry + dispatch; small. |
| 119 | `file_state.py` | **POLISH** | Verify it is only used by `EditFileTool` for read-before-edit gating; if yes, fold into `filesystem_guard.py` from §2.10. |
| 75 | `spawn.py` | **PASS** | `spawn` tool; thin wrapper over `SubagentManager`. |
| 57 | `sandbox.py` | **PASS** | Bubblewrap layout; security-critical. |

### 11.4 `pythinker/api/` (446 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 1 | `__init__.py` | **PASS** | Empty. |
| 445 | `server.py` | **PASS** | OpenAI-compatible HTTP server. `create_app(agent_loop, ...)` takes a pre-built loop — no provider construction lives here. |

### 11.5 `pythinker/auth/` (274 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 66 | `__init__.py` | **PASS** | Auth helpers re-export. |
| 123 | `oauth_remote.py` | **PASS** | Codex OAuth / Copilot OAuth flows; tight. |
| 64 | `refresh_lock.py` | **PASS** | File lock for token refresh. |
| 21 | `pkce.py` | **PASS** | PKCE helper. |

### 11.6 `pythinker/bus/` (102 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 6 | `__init__.py` | **PASS** | Re-exports. |
| 52 | `events.py` | **PASS** | Event dataclasses. |
| 44 | `queue.py` | **PASS** | Two-queue MessageBus. The runtime spine; do not touch. |

### 11.7 `pythinker/channels/` (7,550 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 6 | `__init__.py` | **PASS** | Re-exports. |
| 2,008 | `websocket.py` | **CARVE** §2.3 | Six-way split into `websocket/`. |
| 1,210 | `telegram.py` | **CARVE** §2.8 | Markdown + media extraction. |
| 911 | `matrix.py` | **POLISH** | The `_NioLoguruHandler` and `_configure_nio_logging_bridge` helpers can move to `pythinker/utils/nio_logging.py`. The `_filter_matrix_html_attribute` regex pipeline can live in a `matrix_html.py` peer. After: ~700 LOC. |
| 695 | `email.py` | **POLISH** | Single class but accreted: split SMTP send and IMAP poll into `email_smtp.py` / `email_imap.py` if they are independent (verify imports first). |
| 694 | `discord.py` | **POLISH** | Same pattern as Telegram — extract markdown→Discord rendering helpers. |
| 542 | `msteams.py` | **PASS** | Already smaller than the Discord/Telegram pair. |
| 471 | `slack.py` | **PASS** | Tight Slack adapter. |
| 364 | `whatsapp.py` | **PASS** | Thin Python ↔ Node bridge wrapper. |
| 363 | `manager.py` | **PASS** | Channel lifecycle + outbound dispatch with retries; recently audited. |
| 204 | `base.py` | **PASS** | `BaseChannel` ABC. |
| 82 | `registry.py` | **PASS** | Name → class + entry-point loader. |

### 11.8 `pythinker/cli/` (11,671 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 1 | `__init__.py` | **PASS** | Empty. |
| 3,411 | `onboard.py` | **CARVE** §2.1 | Step-per-file split. |
| 3,240 | `commands.py` | **CARVE** §2.2 | Six-cluster split. |
| 521 | `doctor.py` | **PASS** | Already linear; one polish pass to drop dead `_v1` checks if any. |
| 177 | `models.py` | **PASS** | Data models for CLI options. |
| 162 | `star_prompt.py` | **PASS** | Optional "rate the project" prompt. Move under a `cli/extras/` if it grows further. |
| 142 | `stream.py` | **PASS** | Streaming-output adapter for the REPL. |

#### 11.8.1 `pythinker/cli/onboard_views/` (837 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 1 | `__init__.py` | **PASS** | |
| 437 | `clack.py` | **POLISH** | The core `clack`-style picker shell. After §2.1 lands, this becomes the only "view" file and can drop the unused fallback paths. |
| 191 | `summary.py` | **PASS** | Summary screen view. |
| 103 | `reset.py` | **PASS** | Reset-existing-config view. |
| 72 | `panels.py` | **PASS** | Panel layout helpers. |
| 45 | `risk_ack.py` | **PASS** | Risk-acknowledgement screen. |
| 33 | `errors.py` | **PASS** | Error-rendering helpers. |

#### 11.8.2 `pythinker/cli/tui/` (~2,400 LOC)

The TUI is a separate `textual`-based interactive surface. It is structured
already into `panes/`, `pickers/`, `screens/` — the layout matches the goal
of this plan.

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 22 | `__init__.py` | **PASS** | |
| 749 | `app.py` | **POLISH** | Split per-screen widgets into `screens/` if they currently live inside `app.py`. After: ~400 LOC for the main app skeleton. |
| 303 | `panes/chat.py` | **PASS** | Chat pane widget. |
| 281 | `layout.py` | **PASS** | Grid layout. |
| 258 | `commands.py` | **PASS** | Slash-command palette. |
| 236 | `pickers/model.py` | **PASS** | Model picker dialog. |
| 214 | `pickers/provider.py` | **PASS** | Provider picker dialog. |
| 170 | `pickers/fuzzy.py` | **PASS** | Shared fuzzy search base. |
| 137 | `theme.py` | **PASS** | Theme + colors. |
| 122 | `panes/editor.py` | **PASS** | Editor pane. |
| 102 | `screens/mcp.py` | **PASS** | MCP servers screen. |
| 86 | `panes/waiting_spinner.py` | **PASS** | |
| 81 | `logging_sink.py` | **PASS** | Routes loguru into the TUI log pane. |
| 75 | `panes/status_bar.py` | **PASS** | |
| 66 | `streaming.py` | **PASS** | TUI streaming buffer. |
| 43 | `pickers/sessions.py` | **PASS** | |
| 42 | `pickers/theme.py` | **PASS** | |
| 37 | `panes/overlay.py` | **PASS** | |
| 36 | `panes/hint_footer.py` | **PASS** | |
| 33 | `screens/help.py` | **PASS** | |
| 24 | `screens/status.py` | **PASS** | |
| 18 | `status_snapshot.py` | **PASS** | |
| 0 | `screens/__init__.py` / `panes/__init__.py` / `pickers/__init__.py` | **PASS** | Empty markers. |

### 11.9 `pythinker/command/` (868 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 6 | `__init__.py` | **PASS** | |
| 695 | `builtin.py` | **CARVE** §2.12 | Group by topic into `builtins/`. |
| 98 | `router.py` | **PASS** | Slash-command router; tight. |
| 69 | `metadata.py` | **PASS** | Command metadata table. |

### 11.10 `pythinker/config/` (1,275 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 32 | `__init__.py` | **PASS** | |
| 561 | `schema.py` | **PASS** | 20 Pydantic models. Each is small; the file is just "the config schema." Splitting just moves the import surface around. Leave it as one file. |
| 363 | `editing.py` | **PASS** | Live-edit helpers; recently audited (commit `d7b5b69` enforced 0600 perms). |
| 247 | `loader.py` | **PASS** | `${VAR}` expansion + load. Critical, do not touch. |
| 72 | `paths.py` | **PASS** | Default-path helpers. |

### 11.11 `pythinker/cron/` (651 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 6 | `__init__.py` | **PASS** | |
| 564 | `service.py` | **PASS** | Cron service; one class plus a couple of helpers. |
| 81 | `types.py` | **PASS** | Cron schema types. |

### 11.12 `pythinker/heartbeat/` (197 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 5 | `__init__.py` | **PASS** | |
| 192 | `service.py` | **PASS** | 30-min heartbeat reader; right size. |

### 11.13 `pythinker/providers/` (4,945 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 42 | `__init__.py` | **PASS** | Re-exports. |
| 1,130 | `openai_compat_provider.py` | **CARVE** §2.6 | Responses-API split + sanitize utility. |
| 798 | `base.py` | **POLISH** | 30 methods on `LLMProvider`. The retry/backoff helpers (`_extract_retry_after*`, `_to_retry_seconds`) can move to `pythinker/providers/_retry.py`. After: ~600 LOC. |
| 682 | `registry.py` | **PASS** | 47 `ProviderSpec` entries. Leave as one declarative table. |
| 601 | `anthropic_provider.py` | **POLISH** | Extract cache-control marker logic to `pythinker/providers/anthropic_cache.py`. |
| 314 | `local_models.py` | **PASS** | Local-model bookkeeping. |
| 273 | `github_copilot_provider.py` | **PASS** | OAuth → token-exchange path. |
| 227 | `openai_codex_provider.py` | **PASS** | Recently extended (allow-list, gpt-5.5 cap). |
| 183 | `azure_openai_provider.py` | **PASS** | Tight Azure adapter. |
| 145 | `factory.py` | **PASS** | `ProviderSnapshot` + factory wiring. |
| 114 | `transcription.py` | **PASS** | Whisper-style transcription helpers. |

#### 11.13.1 `pythinker/providers/openai_responses/` (436 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 29 | `__init__.py` | **PASS** | |
| 297 | `parsing.py` | **PASS** | Responses-API decoder. The §2.6 split lands its handler-class peer here. |
| 110 | `converters.py` | **PASS** | Message converters. |

### 11.14 `pythinker/release/` (463 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 12 | `__init__.py` | **PASS** | |
| 451 | `checks.py` | **PASS** | Pre-release checklist; declarative `CheckResult`/`CheckReport`. Right shape. |

### 11.15 `pythinker/runtime/` (843 LOC) — governed-execution chokepoint

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 1 | `__init__.py` | **PASS** | |
| 67 | `_bootstrap.py` | **PASS** | Bootstrap glue; tight. |
| 96 | `egress.py` | **PASS** | `ToolEgressGateway`; small and focused. |
| 96 | `manifest.py` | **PASS** | `AgentManifest`; declarative. |
| 232 | `policy.py` | **PASS** | `PolicyDecision` is just `(allowed: bool, reason: str)`. No `behavior` field exists. The Phase-3 approval-aware redesign is in the spec, not the code, and is out of scope here. |
| 237 | `telemetry.py` | **PASS** | Telemetry sinks; ABC + three impls. Right size. |
| 114 | `context.py` | **PASS** | `RequestContext` + `BudgetCounters` dataclasses. |

### 11.16 `pythinker/security/` (121 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 1 | `__init__.py` | **PASS** | |
| 120 | `network.py` | **PASS** | SSRF block-list. Security-critical, do not touch. |

### 11.17 `pythinker/session/` (673 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 5 | `__init__.py` | **PASS** | |
| 668 | `manager.py` | **POLISH** | Two classes (`Session`, `SessionManager`). Split persistence (`save`, `_load`, `_repair`, `iter_message_files_for_search`, sidecar paths) into `session_storage.py`. `manager.py` keeps the cache + lifecycle. After: ~350 LOC each. |

### 11.18 `pythinker/skills/` (745 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 378 | `skill-creator/scripts/init_skill.py` | **PASS** | Template generator. The 8 TODO strings are intentional placeholders. Don't touch. |
| 213 | `skill-creator/scripts/quick_validate.py` | **PASS** | Canonical skill validator. Used by `tests/test_agents_skills.py`. |
| 154 | `skill-creator/scripts/package_skill.py` | **PASS** | Skill packager. |

(Plus skill assets — markdown only, not Python.)

### 11.19 `pythinker/templates/` (0 LOC of Python; runtime Jinja2 assets)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 0 | `__init__.py` | **PASS** | The `.py` files are empty markers; the directory itself holds the runtime Jinja2 prompt templates loaded by `pythinker/utils/prompt_templates.py:14`. **Do not delete or repurpose.** |
| 0 | `memory/__init__.py` | **PASS** | Same — sub-directory marker for memory-related templates. |

### 11.20 `pythinker/utils/` (2,567 LOC) — utility grab-bag

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 6 | `__init__.py` | **PASS** | |
| 540 | `helpers.py` | **POLISH** | 23 unrelated functions. Split by topic: `text.py` (strip_think, truncate_text, build_image_content_blocks, image_placeholder_text, stringify_text_blocks, find_legal_message_start), `tokens.py` (estimate_prompt_tokens*), `tool_results.py` (maybe_persist_tool_result, _bucket_mtime, _cleanup_tool_result_buckets, _render_tool_result_reference, _write_text_atomic), `messages.py` (build_assistant_message, build_status_content, split_message), `time.py` (timestamp, current_time_str, safe_filename), `workspace.py` (ensure_dir, sync_workspace_templates, detect_image_mime). Net delta: ~0 LOC, big readability win. |
| 506 | `update.py` | **PASS** | In-place upgrade machinery. Touchy; leave it. |
| 390 | `gitstore.py` | **PASS** | dulwich wrapper for the dream auto-commit; right size. |
| 293 | `document.py` | **PASS** | Multi-format text extractor; one function per format. |
| 168 | `searchusage.py` | **PASS** | Tavily usage parsing. |
| 137 | `tool_hints.py` | **PASS** | Tool-hint formatter. |
| 107 | `path.py` | **PASS** | Path-resolution helpers. |
| 97 | `runtime.py` | **PASS** | Runtime helpers. |
| 89 | `evaluator.py` | **PASS** | Tiny safe-eval shim; small. |
| 82 | `restart.py` | **PASS** | Restart-the-world helper. |
| 62 | `log.py` | **PASS** | Loguru config. |
| 55 | `media_decode.py` | **PASS** | Media decoding (data URIs, etc). |
| 35 | `prompt_templates.py` | **PASS** | Tiny string-template helper. |

### 11.21 `pythinker/web/` (6 LOC)

| LOC | File | Verdict | Note |
|---:|---|---|---|
| 6 | `__init__.py` | **PASS** | Marks the location of the bundled `dist/` build (force-included into the wheel). |

---

## 12. Summary of decisions per file

Tally across §11:

| Verdict | Files | Notes |
|---|---:|---|
| **CARVE** (covered by phases A–E) | 12 | The largest LOC offenders |
| **POLISH** (single-PR cleanup) | 17 | Mid-size files with one obvious split (incl. `pdf.py` after correction) |
| **PASS** (do not touch) | ~120 | Already at right size, *or* part of the documented public-import surface, *or* (`templates/`) loaded as runtime data |
| **DEAD?** (verify then remove) | 0 | (Earlier draft flagged `usage.py`; that turned out to be wrong — see §11.3 / §15.) |
| **API** (do not touch) | 6 | `__init__.py`, `pythinker.py`, `channels/base.py`, `bus/events.py`, `bus/queue.py`, `config/schema.py` |

**Total runtime files audited:** 163. **Files marked for change:** 30
(18%). **Files explicitly judged correct as-is:** ~120 (74%).

The 70% pass rate is the headline finding: most of the codebase is already
in good shape. The work is concentrated in a small number of high-LOC files
that have grown beyond their original responsibilities — exactly where
"prefer simplicity" pays the highest rent.

---

## 13. Phase ↔ file mapping (for the implementer)

When a phase from §3 lands, here is the exact set of files it should touch:

### Phase A
- A1 (onboard split): `pythinker/cli/onboard.py` → split into
  `pythinker/cli/onboard_steps/*.py` + `onboard_options.py` + `onboard_auth.py`
  + `onboard_types.py`, while `onboard.py` remains the compatibility surface
  for current private imports and monkeypatch targets. Tests under
  `tests/cli/test_onboard*.py` and `tests/config/test_config_migration.py`.
- A2 (memory split): `pythinker/agent/memory.py` → `pythinker/agent/memory/`
  package. Preserve `pythinker.agent.memory.estimate_message_tokens` as the
  authoritative monkeypatch target for consolidation tests. Tests under
  `tests/agent/test_memory*.py`, `test_consolidator.py`, `test_dream.py`.
- A3 (pdf — optional polish only): `pythinker/agent/tools/pdf.py` is
  generation-only. If the maintainer wants the polish, extract inline CSS
  template + font metric tables to
  `pythinker/agent/tools/pdf_assets.py`. Otherwise skip A3 entirely. The
  earlier "render vs. extract" framing was wrong; PDF text extraction
  already lives in `pythinker/utils/document.py::_extract_pdf` and is not
  in scope. Tests under `tests/agent/tools/test_pdf*.py`.
- A4 (admin split): `pythinker/admin/service.py` → `+ snapshot.py + redact.py`.
  Tests under `tests/admin/`.
- A5 (command split): `pythinker/command/builtin.py` remains a legacy shim;
  implementations move to
  `pythinker/command/builtins/{lifecycle,dream,tasks,format}.py`.
  Preserve old import and patch targets on `pythinker.command.builtin`. Tests
  under `tests/command/` plus `tests/cli/test_restart_command.py`.

### Phase B
- B1 (runner phases): `pythinker/agent/runner.py` — no file split, only
  internal method extraction. Tests under
  `tests/agent/test_runner*.py`.
- B2 (loop carve): `pythinker/agent/loop.py` →
  `+ loop_tools.py + loop_reload.py + loop_context.py +
  pythinker/providers/limits.py`. Tests under `tests/agent/`,
  `tests/runtime/`, `tests/session/`.

### Phase C
- C1 (openai_compat split): `pythinker/providers/openai_compat_provider.py`
  → `+ openai_responses/{chat,parsing,circuit}.py +
  pythinker/providers/_message_sanitize.py`. Tests under
  `tests/providers/`.

### Phase D
- D1 (websocket split): `pythinker/channels/websocket.py` →
  `pythinker/channels/websocket/{channel,auth,multiplex,rest,media,config}.py`.
  Tests under `tests/channels/`.
- D2 (telegram extract): `pythinker/channels/telegram.py` →
  `+ telegram_markdown.py + telegram_media.py`. Tests under
  `tests/channels/test_telegram*.py`.
- D3 (filesystem guard): `pythinker/agent/tools/filesystem.py` →
  `+ filesystem_guard.py`. Tests under `tests/agent/tools/`.

### Phase E
- E1 (commands split): `pythinker/cli/commands.py` →
  `+ pythinker/cli/{repl,updates,bootstrap,serve,render}.py`. Tests under
  `tests/cli/`.

### Cross-cutting (single-PR)
- ~~X1 §4.1:~~ withdrawn — `pythinker/templates/` is the runtime Jinja2 root.
- ~~X2 §4.2:~~ withdrawn — `PolicyDecision.behavior` does not exist.
- X3 §4.3: extract `_clamp_context_window` to
  `pythinker/providers/limits.py` (also part of B2).
- X4 §4.4: re-opened narrowly. CLI ↔ API was withdrawn (api/server.py
  takes a pre-built loop), but `pythinker/pythinker.py:159` still
  defines a parallel `_make_provider`. Phase E must verify both
  delegate to `providers.factory.make_provider`. Action item, not its
  own PR — folds into E1.
- ~~X5~~ withdrawn — both `usage.py` and `usage_ledger.py` are live
  with distinct callers (websocket + admin import the former; loop
  imports the latter). Nothing to delete.

### Polish-only (each its own small PR)
- P1: `pythinker/agent/subagent.py` (flatten announce branches).
- P2: `pythinker/agent/tools/mcp.py` (TODO sweep).
- P3: `pythinker/agent/tools/search.py` (collapse render-row branches).
- ~~P4: `pythinker/api/server.py`~~ — withdrawn (no provider-build code lives here).
- P5: `pythinker/channels/matrix.py` (extract logging bridge + html
  filter).
- P6: `pythinker/channels/email.py` (smtp / imap split if independent).
- P7: `pythinker/channels/discord.py` (extract render helpers).
- P8: `pythinker/cli/doctor.py` (drop dead `_v1` checks).
- P9: `pythinker/cli/onboard_views/clack.py` (drop unused fallback paths
  after A1 lands).
- P10: `pythinker/cli/tui/app.py` (move screens out).
- P11: `pythinker/providers/base.py` (extract retry helpers).
- P12: `pythinker/providers/anthropic_provider.py` (extract cache-control).
- P13: `pythinker/session/manager.py` (split storage from cache).
- P14: `pythinker/utils/helpers.py` (six-way topic split).

That's 11 carve work-items + 1 cross-cutting (X3; X1/X2/X5 withdrawn,
X4 re-folded into E1 as an action item rather than its own PR) + 13
polish (P1–P14, with P4 withdrawn) = **25 work-item PRs** at the
ceiling. Realistically, the maintainer will pick the ones that hurt
most and stop when the cost ratio inverts.

---

## 14. Parking lot

Issues raised in review that are explicitly **not** fixed by this plan:

- The TUI's import of `_make_provider` / `_load_runtime_config` /
  `_load_browser_config` from `pythinker/cli/commands.py:276` is the
  only cross-CLI-module dependency the plan currently has to manage.
  See "Cross-module callers to keep working" under Phase E.
- `pythinker/agent/runner.py` has both `execute()` (internal step at
  line 57) and `run()` (public entry at line 277). Section 2.5 targets
  `run()`. If the implementer also wants to flatten `execute()` into
  `run()`, that is a *separate* PR (its own design call) and is not
  included in the §3 LOC delta estimates.

## 15. Errata changelog

| Pass | Date | What changed |
|---|---|---|
| 1 → 2 | 2026-05-04 | First post-review correction. Withdrew §4.1 (templates), §4.2 (PolicyDecision.behavior), §4.4 (api/server provider unify), §2.9 (pdf render/extract). Widened §1 public-surface fence. |
| 2 → 3 | 2026-05-04 | Second post-review correction. 12 fixes: relocated to `.agents/plans/` (maintainer scope per `.agents/README.md`); reframed §1 single-user principle so it does not steer past `SECURITY.md` defenses; corrected `AgentRunner.execute` → `run`; removed `usage.py` "DEAD?" marking after confirming live websocket+admin imports; reconciled §9 with §1 on the public-surface list; replaced `parse.py` with `parsing.py`; pulled the missing autonomous-task-spine spec citation; switched branch-target guidance to `dev` per `CONTRIBUTING.md:30,66`; added Phase E TUI cross-import warning; corrected several stale test paths and marked admin service tests as a gap; fixed top-10 LOC sum (16,798) and percentages (36% / 24%); de-emphasized the context-norm "duplication" claim now that the shared helper is acknowledged. |
| 3 → 4 | 2026-05-04 | Third post-review correction. Defined the unit of shipping as **work-item PR**, not phase-PR (§3, §A–E summaries). Trimmed §1.1 to the personal-agent ceiling — "don't remove what already ships, don't add new layers either" (no RBAC / two-tier tokens / Origin allowlists). Re-opened §4.4 narrowly: `pythinker/pythinker.py:159` also defines `_make_provider`; Phase E folds reconciliation in as an action item rather than a separate PR. Withdrew §4.5 (wrong packaging lever). Added the **import/patch compatibility checklist** to §6 and an explicit private-import audit to §2.3. Corrected the `AgentLoop.__init__` shrink claim — file moves don't drop ctor inputs (§2.4 + §8). Split §8 metrics into "net deletion" vs "top-heavy concentration" so pure file-moves stop inflating the headline. Fixed §10 + §0 banner: implementation plans land in `.agents/plans/`, not `docs/superpowers/plans/`. Fixed §12 tally row (DEAD? → 0). Trimmed `InboundMessage` overstatement in §1 principle 6. Replaced §9 worktree question (unprovable spec citation) with a default + opt-in. Updated runner row in §11.3 + top-10 row in §0 to reference `run()` instead of `execute()`. Trimmed the §0 errata banner. |
| 4 → 5 | 2026-05-04 | Fourth post-review correction (this revision). Added explicit compatibility contracts for `pythinker.cli.onboard`, `pythinker.agent.memory.estimate_message_tokens`, and `pythinker.command.builtin` import/patch targets; fixed the nonexistent admin service test reference; reconciled "tests follow code" with the no-test-edits split-PR policy via an in-place `**Correction.**` block on §1 rule 4 (the rule itself was rewritten, not appended) and tightened §2.3 to defer to the §6 audit instead of duplicating a partial export list. |
| Shipped | 2026-05-05 | Plan complete. Phase A (#3, #4, #5, #6 — A3 pdf assets skipped as optional polish), Phase B (#7, #8), Phase D (#9, #10, #11), Phase E (`7d608c1` — 5-file split into bootstrap/render/repl/serve/updates), Phase C (`0e26f76` — `openai_responses/{chat,circuit}.py` + `_message_sanitize.py`). Cross-cutting §4.3 landed with B7; §4.4 verified inline (SDK `_make_provider` and CLI `_make_provider` both delegate to `providers.factory.make_provider`, SDK has no typer/rich imports). Verification end-state: `ruff check pythinker --select F401,F841` clean, `pytest tests/` 3189 passed / 1 skipped, `pythinker doctor` green. |

---

*End of plan. No code is to be written from this document until §10 is
satisfied.*
