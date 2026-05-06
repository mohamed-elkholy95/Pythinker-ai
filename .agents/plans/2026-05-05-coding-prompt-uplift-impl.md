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

## 2. Phase 2 — Subagent role split (deferred)

Defer to a separate approval after Phase 1 ships. Audit §4 Phase 2
covers the design (`coder` / `explore` / `plan` roles, tool gating,
`spawn.py` schema change). Not in this PR.

## 3. Phase 3 — Compaction prompt upgrade (deferred)

Defer. Audit §4 Phase 3 covers the design (consolidator template
becomes structured tags). Not in this PR.

## 4. Phase 4 — `/init` AGENTS.md generator (deferred)

Defer. Audit §4 Phase 4 covers the design (slash command + template).
Not in this PR.

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

- [ ] Maintainer: approve Phase 1 (this PR's scope).
- [ ] Maintainer: confirm channel guard wraps the parallelism +
      system-reminder paragraphs (not the whole block).
- [ ] Maintainer: confirm token budget — Phase 1 alone adds ~+0.7 k to
      the CLI root system prompt (audit §7 line "After Phase 1").

Once all three are checked, an implementer may begin Phase 1.

---

## 8. Errata changelog

| Pass | Date | What changed |
|---|---|---|
| 0 | 2026-05-05 | Initial cut from audit `2026-05-05-coding-prompt-uplift.md` §4 Phase 1. Phases 2–5 deferred to their own gates. |
