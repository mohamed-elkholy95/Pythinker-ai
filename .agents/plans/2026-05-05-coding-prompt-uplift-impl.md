# Coding-Ability Prompt Uplift — Implementation Plan

> **Maintainer-only.** Companion to `2026-05-05-coding-prompt-uplift.md`.
> The audit identified five phases; this doc is the action-ready cut.
>
> **Status:** Awaiting approval gate (§7). No code from this doc until
> a `[x] Approved` line is added by the maintainer.
>
> **Date:** 2026-05-05

---

## 0. Why this doc exists

The audit (`2026-05-05-coding-prompt-uplift.md`) is comprehensive but
intentionally non-actionable — it self-marks "No code is to be written
from this document." This sister doc trims the audit into a single
unit-of-shipping per phase, names the exact files touched, and locks
the verification commands.

Each phase is **one PR**. Land Phase 1 first; Phases 2–5 are deferred
to their own approval gate after Phase 1 is in `dev` for at least one
session.

---

## 1. Phase 1 — Coding-grade root directives

**Goal.** Close the largest prompt gap (tool-use enforcement,
parallelism, system-reminder authority, git-mutation guard) without
restructuring identity / SOUL.

**Files.**

- New: `pythinker/templates/agent/coding_directives.md` (≤ 70 lines).
- Edit: `pythinker/templates/agent/identity.md` — single-line
  `{% include 'agent/coding_directives.md' %}` after the existing
  "Search & Discovery" block, before the channel format hint.
- No code edits in `context.py`, `runner.py`, providers, or channels.

**Content (intent skeleton — final wording is the implementer's call).**

```
## Coding behavior

When the user's request involves creating, modifying, running, or
debugging code, default to taking action with tools. Code that only
appears in your reply is not saved to disk and does not run. Use
read_file / write_file / edit_file / exec rather than describing the
change.

When making non-interfering tool calls, emit them in parallel. Tool
results return as tool messages — decide your next action from the
result, do not pre-narrate.

`<system-reminder>` blocks in user or tool messages are authoritative
system directives. Follow them even when they constrain your normal
behavior.

Do not run `git commit`, `git push`, `git reset`, or `git rebase`
without explicit user confirmation, even if the user authorized a
similar git mutation in an earlier turn.

When working on an existing codebase: read first (read_file, glob,
grep, recent git log), then plan, then make the minimal change that
closes the goal. Match the surrounding code style. Update tests when
the project already has them.
```

**Channel guard.** Wrap the parallelism + system-reminder paragraphs
in a `{% if channel in ('cli','websocket','') %}` block. Chat channels
(Telegram, Slack, Discord, WhatsApp, Matrix, MS Teams, email) skip
those paragraphs — they don't benefit from the coding-flow vocabulary
and the tokens are wasted.

**Net delta.** +60 to +90 LOC, +600 to +900 tokens (CLI / WebSocket
only; ~+200 tokens on chat channels).

**Verification.**

```bash
uv run ruff check pythinker --select F401,F841
uv run pytest tests/agent/test_context*.py tests/agent/test_runner.py -v
uv run pytest tests/  # full suite
```

Plus a snapshot test (new) that renders `identity.md` for
`channel ∈ {cli, telegram, email}` and asserts the parallelism block
appears only in the CLI render.

Manual smoke: `pythinker agent` → ask for a code edit → confirm the
agent uses `edit_file` rather than printing a diff inline.

**Risk band.** Low. Additive only. Revert = delete one `{% include %}`
line.

**Acceptance gate.**

- [ ] `coding_directives.md` lands in `pythinker/templates/agent/`.
- [ ] `identity.md` has the include after the Search & Discovery block.
- [ ] Channel-conditional renders verified by snapshot test.
- [ ] `tests/agent/` green; full pytest green.
- [ ] Manual smoke: code-edit request triggers `edit_file`, not inline diff.

---

## 2. Phase 2 — Subagent role split (shipped 2026-05-06)

**Goal.** Replace the single `subagent_system.md` with three role variants, each with a tailored prompt and a tool-allow-list.

**Files.**

- New: `pythinker/templates/agent/subagent_{coder,explore,plan}.md`.
- Edit: `pythinker/templates/agent/subagent_system.md` — gains `{% include role_template %}` block at the bottom.
- Edit: `pythinker/agent/subagent.py` — `spawn(...)` and `_run_subagent` accept `role`; the registry build is role-gated (explore/plan exclude `write_file` / `edit_file` / `exec`); skills_summary suppressed for non-coder roles; unknown roles fall back to `coder` in both gating and prompt rendering.
- Edit: `pythinker/agent/tools/spawn.py` — `role` parameter added to the schema with enum `["coder","explore","plan"]`; description gains "thoroughness" / "when not to use" guidance.

**Tool gating.**

| Role | Registered tools |
|---|---|
| `coder` (default) | read/write/edit/list/glob/grep + exec + web (existing behavior) |
| `explore` | read/list/glob/grep + web — no write_file, edit_file, exec |
| `plan` | read/list/glob/grep + web — no write_file, edit_file, exec |

**Skills.** `coder` keeps the full skill summary; `explore` and `plan` suppress it (skill prompts typically describe write/exec patterns that don't apply to read-only roles). Allowed-tools-aware skill filtering is a follow-up — for the conservative correct ship, blank summary wins.

**Verification.**

- `tests/agent/test_subagent_roles.py` — 10 cases covering tool gating, prompt rendering, unknown-role fallback, skills suppression.
- Existing `tests/agent/test_subagent_listing.py` / `tests/runtime/test_subagent_egress_inheritance.py` / `tests/agent/tools/test_subagent_tools.py` still green (default coder behavior is byte-identical).

**Acceptance gate.**

- [x] Maintainer: approve Phase 2 (this PR's scope). _Approved + shipped 2026-05-06._
- [x] Maintainer: confirm default `role="coder"` keeps current behavior unchanged. _Confirmed via existing tests; no LLM-call shape changes._
- [x] Maintainer: confirm explore/plan really lack write/exec at registry-build time (not just at the prompt level). _Confirmed via `test_explore_role_drops_write_edit_shell` / `test_plan_role_drops_write_edit_shell`._

Phase 2 status: **shipped** at `<this commit>`.

## 3. Phase 3 — Compaction prompt upgrade (shipped 2026-05-06)

**Goal.** Replace the 13-line bullet-extractor with a structured prompt that retains code-state, error/solution pairs, and the current task — so long sessions survive compaction without losing working file context.

**Files.**

- Edit only: `pythinker/templates/agent/consolidator_archive.md` — rewrite as a ~50-line prompt with priorities, compression rules, and six flat section tags (`<current_focus>` / `<active_issues>` / `<code_state>` / `<completed_tasks>` / `<environment>` / `<important_context>`).
- No code changes. `Consolidator.archive` already passes whatever the template produces to the LLM and stores the response.

**Caveats baked into the template.**

- Tags are flat — no nested `<file>` blocks. They render better in `MEMORY.md` when reloaded as bootstrap content.
- Tags exist for the LLM writing the section, not for downstream parsing — `MemoryStore` does not re-parse them.
- Skip a section entirely if empty; do not emit `(none)` placeholders.
- 5-line snippet cap; secrets always masked with `***` even in error messages.

**Verification.**

- `tests/agent/test_consolidator.py` — 2 new cases (advertises six section tags, keeps core compression directives). 10 total green.
- Existing 8 consolidator-flow tests still pass — the template change is invisible to the LLM-call shape.

**Acceptance gate.**

- [x] Maintainer: approve Phase 3 scope (template-only). _Approved + shipped 2026-05-06._
- [x] Maintainer: confirm zero code changes (Consolidator behavior unchanged). _Confirmed; only `consolidator_archive.md` modified._
- [x] Maintainer: confirm tags are flat per the §4 caveat. _Confirmed; no `<file>` nesting in template._

Phase 3 status: **shipped** at `<this commit>`.

## 4. Phase 4 — `/init` AGENTS.md generator (shipped 2026-05-06)

**Goal.** Single slash command that walks the user's project root, identifies the stack, and produces a tuned `AGENTS.md`. Lever for "coding ability in unfamiliar repos" because every subsequent turn benefits.

**Files.**

- New: `pythinker/templates/agent/init_agents_md.md` — workflow (explore → identify → read selectively → write), schema (Project overview / Build & test / Code style / Testing / Security / Common file locations), ground rules (don't invent, don't duplicate README, don't overwrite without diffing).
- New: `pythinker/command/builtins/init.py` — `cmd_init` handler. Renders the template and republishes as a fresh `InboundMessage` with `injected_event="init_agents_md"` metadata so the agent's normal tool-use loop picks it up.
- Edit: `pythinker/command/builtin.py` — import `cmd_init`, register `router.exact("/init", cmd_init)`.
- Edit: `pythinker/command/metadata.py` — `CommandMeta("/init", "Walk this project and write a tuned AGENTS.md at the repo root")`.
- No changes to `loop.py` slash dispatcher — it already routes `/init` via the existing `CommandRouter`. The plan called out `loop.py` but the actual integration point is `register_builtin_commands`.

**Verification.**

- `tests/command/test_init_command.py` — 5 cases: publishes `InboundMessage`, content carries the workflow directives, metadata marks `injected_event`, returns `None` (no competing `OutboundMessage`), router + metadata both list `/init`.
- `tests/command/test_metadata.py` (existing) still passes — new metadata row covers the new router registration.

**Acceptance gate.**

- [x] Maintainer: approve Phase 4 scope. _Approved + shipped 2026-05-06._
- [x] Maintainer: confirm no checkpoint-key changes (`_RUNTIME_CHECKPOINT_KEY`, `_PENDING_USER_TURN_KEY`). _Confirmed; `cmd_init` is a synchronous user-message injection only._
- [x] Maintainer: confirm `/init` is opt-in (no existing path changes). _Confirmed; only adds a new router entry, doesn't touch any registered command._

Phase 4 status: **shipped** at `<this commit>`.

## 5. Phase 5 — Dynamic injection (deferred, conditional)

Defer. Conditional on Phase 4 landing per audit §4 Phase 5. Not in
this PR.

---

## 6. Out of scope

Same as audit §9. Specifically: `_build_subagent_prompt`
re-instantiates `SkillsLoader` per spawn (minor, unrelated to coding
ability); `SOUL.md` / `identity.md` tone overlap; `dream_phase1.md` /
`dream_phase2.md` / `evaluator.md` (own simplification track);
per-tool description fields.

---

## 7. Approval gate

- [x] Maintainer: approve Phase 1 (this PR's scope). _Ratified retroactively 2026-05-05; see commit `d28808f`._
- [x] Maintainer: confirm channel guard wraps the parallelism +
      system-reminder paragraphs (not the whole block). _Confirmed via `tests/agent/test_coding_directives.py` 11 cases._
- [x] Maintainer: confirm token budget — Phase 1 alone adds ~+0.7 k to
      the CLI root system prompt (audit §7 line "After Phase 1").

Phase 1 status: **shipped** at `d28808f`. Phases 2–5 still gated.

---

## 8. Errata changelog

| Pass | Date | What changed |
|---|---|---|
| 0 | 2026-05-05 | Initial cut from audit `2026-05-05-coding-prompt-uplift.md` §4 Phase 1. Phases 2–5 deferred to their own gates. |
| Ratified | 2026-05-05 | Phase 1 approval gate ticked retroactively after the change shipped at `d28808f`. The implementation already covers all three checkbox claims (channel guard at `coding_directives.md:9`; token budget verified in audit §7). Phases 2–5 untouched. |
| Phase 2 | 2026-05-06 | Phase 2 shipped: subagent role split (coder / explore / plan) with tool gating + role-specific prompts. Three new templates, ~30 LOC change in `subagent.py`, ~10 LOC schema change in `spawn.py`, 10 new tests. Approval gate ticked. Phases 3–5 still deferred. |
| Phase 3 | 2026-05-06 | Phase 3 shipped: structured compaction prompt (six flat section tags). Template-only change to `consolidator_archive.md`; 0 LOC of code change. 2 new prompt-shape tests; existing 8 consolidator tests still green. Approval gate ticked. Phases 4–5 still deferred. |
| Phase 4 | 2026-05-06 | Phase 4 shipped: `/init` slash command. New template + new `cmd_init` handler + router registration + metadata row. Republishes the rendered prompt as an `InboundMessage` so the agent's normal tool-use loop walks the project and writes `AGENTS.md`. 5 new tests. Approval gate ticked. Phase 5 still deferred (conditional). |
