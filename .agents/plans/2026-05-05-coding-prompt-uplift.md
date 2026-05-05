# Coding-Ability Prompt Uplift Plan

> **Maintainer-only.** Lives under `.agents/plans/` per
> `.agents/README.md` § Boundaries. Do not link from `README.md`,
> `AGENTS.md`, the wheel, or any user-facing surface.
>
> **Status:** Draft. Comprehensive scan of Pythinker's prompt layer
> against a stricter "coding-grade" prompt taxonomy that has been studied
> locally on the maintainer's machine. **No code is to be written from
> this document.** It is the input to a follow-up implementation plan.
>
> **Date:** 2026-05-05
>
> **Scope:** Pythinker's coding ability — i.e. how the agent reasons
> about reading, writing, running, and refactoring code. The lever is
> the prompt layer (root system prompt, subagent prompts, dynamic
> injections, init/compact prompts, spawn-tool description). No
> provider, channel, or runtime-spine changes are proposed.

---

## 0. Audit method

1. Walked every prompt surface in `pythinker/templates/{,agent/}` and
   the assemblers in `pythinker/agent/context.py` and
   `pythinker/agent/subagent.py:_build_subagent_prompt`.
2. Compared against a coding-grade prompt taxonomy along five axes:
   (a) coding-action directives, (b) subagent role split,
   (c) dynamic context injection, (d) compaction prompt,
   (e) tool descriptions.
3. The reference design is studied locally outside the repo and is
   not linked from any committed surface; only the *patterns* are
   carried into this plan.

---

## 1. Inventory — Pythinker prompt surface (current)

| File | Role | LOC | Notable signals |
|---|---|---:|---|
| `templates/agent/identity.md` | Identity + runtime block | 39 | Workspace paths, channel format hint, search-discovery rules, untrusted-content snippet, PDF deliverable contract |
| `templates/SOUL.md` | Persona / execution rules | 21 | "Solve by doing", "act on single-step / outline multi-step", "read before write" |
| `templates/AGENTS.md` | Default workspace AGENTS.md | 19 | Cron + heartbeat usage hints |
| `templates/USER.md` | User-profile template | 49 | Form-style |
| `templates/TOOLS.md` | Tool catalog injected via bootstrap | 26 (avg) | Loaded as a bootstrap file by `ContextBuilder._load_bootstrap_files` |
| `templates/agent/skills_section.md` | Skills index header | 7 | "Read SKILL.md to use a skill" |
| `templates/agent/platform_policy.md` | OS-specific note | 11 | Two-branch Windows / POSIX |
| `templates/agent/subagent_system.md` | **Single** subagent prompt | 20 | Generic "stay focused" + workspace + skills |
| `templates/agent/subagent_announce.md` | Subagent spawn announcement | (small) | Out of scope here — not part of the role-prompt surface |
| `templates/agent/consolidator_archive.md` | Memory consolidation prompt | 13 | Bullet extraction by category |
| `templates/agent/dream_phase1.md` / `dream_phase2.md` / `evaluator.md` | Dream-mode prompts | — | Out of scope for "coding ability" |
| `templates/agent/_snippets/untrusted_content.md` | Untrusted-content reminder | (snippet) | Included from identity & subagent |
| `agent/context.py` `build_system_prompt()` | Assembler | ~30 | identity + bootstrap (AGENTS/SOUL/USER/TOOLS.md) + memory + skills + recent history, joined with `\n\n---\n\n` |
| `agent/subagent.py` `_build_subagent_prompt()` | Subagent assembler | ~16 | Single template, no role split |
| `agent/tools/spawn.py` `SpawnTool.description` | Spawn tool blurb | ~5 lines | One generic description |

**Total ≈ 200 LOC of prompt-layer content.** No dynamic injection, no
role split, no `/init` generator, weak compaction prompt, terse spawn-tool
description.

## 2. Gap analysis (vs the coding-grade prompt taxonomy)

| Axis | Pythinker today | Stronger pattern | Coding-ability impact |
|---|---|---|---|
| Tool-use directives | "Read before you write" + "Solve by doing" (~5 lines in `SOUL.md`) | Explicit "if it involves creating/modifying/running code, you MUST use tools — do not just describe" + parallelism guidance + system-reminder authority + git-mutation guard | **High.** The biggest single gap. The agent drifts toward describing fixes instead of editing files when the model is uncertain. |
| Subagent roles | One generic `subagent_system.md` | `coder` / `explore` / `plan` variants with per-role tool gates and tailored role text | **High.** A single prompt + the full toolset means the spawn tool can't be used for fast read-only exploration without ad-hoc prompt-engineering on every call. |
| Dynamic injection | None | Plan-mode + AFK-mode periodic reminders | **Medium.** Long sessions silently lose plan-mode discipline; AFK-mode (no-human-on-channel) is not currently a Pythinker concept. |
| Compaction | 13-line bullet extractor | Structured compactor with code-state retention rules (current focus / environment / completed / active issues / code state) | **Medium.** The current consolidator throws away post-fix code-state context the next turn could have used. |
| Init / AGENTS.md gen | Not present | A dedicated prompt that walks the project, then writes a project-tuned AGENTS.md | **Low–Medium.** Pythinker has no `/init`; would be a small new feature, not just a prompt swap. |
| Spawn tool description | 5-line generic | Detailed description with thoroughness levels + when-not-to-use examples | **Medium.** Routing-quality lever. |

---

## 3. Hard constraints (non-negotiable)

These come from `CLAUDE.local.md`, project-wide `AGENTS.md`, and the
existing `2026-05-04-simplification-alignment.md` plan.

1. **Simplicity first.** The plan must add the *minimum* prompt content
   that closes each gap. No abstractions, no flexibility, no
   speculative hooks.
2. **Surgical changes.** Touch only the prompt layer + the two
   assemblers that consume it. No runner, channel, provider, or schema
   changes outside what each phase explicitly calls out.
3. **No upstream / external project mentions in committed files.**
   Templates, code comments, commit messages, PR descriptions, docs,
   and tests must not name external reference designs. Patterns are
   carried in by *shape*, not by attribution.
4. **Channel-friendly.** Pythinker is multi-channel (Telegram,
   WhatsApp, email, …). Tool-use directives must not assume a CLI
   session — they need to be channel-aware via `{% if channel in (...) %}`
   guards in `identity.md`.
5. **Token budget.** The current root system prompt assembles to
   roughly 2–3 k tokens depending on workspace. The plan caps net
   growth at **~1,200 tokens** for the root system prompt and **~600
   tokens** for each subagent variant.

---

## 4. Adoption plan — five phases

Phases are ordered by **impact-per-LOC**, lowest risk first. Each phase
is independently shippable; later phases assume earlier phases landed.

### Phase 1 — Coding-grade root directives (additive, low risk)

**Goal.** Close the largest gap (tool-use enforcement, parallelism,
system-reminder authority, git-mutation guard) without restructuring
the existing identity / SOUL split.

**Files touched.**

- New: `pythinker/templates/agent/coding_directives.md` — single Jinja
  template, ≤ 70 lines, channel-aware via `{% if channel in (...) %}`.
- Edit: `pythinker/templates/agent/identity.md` — add one line:
  `{% include 'agent/coding_directives.md' %}` after the existing
  "Search & Discovery" block, before the channel format hint.
- Edit: `pythinker/templates/TOOLS.md` — verify it does not already
  duplicate the directives we are about to add; trim overlap if any.
- No change to `context.py`, `runner.py`, or any provider.

**Content (final wording is left to the implementation plan; this is
the intent skeleton, in Pythinker's voice and tool names).**

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
in a `{% if channel in ('cli','websocket','') %}` block — chat
channels rarely benefit from the coding-flow vocabulary and the tokens
are wasted.

**Estimated delta.** +60 to +90 LOC, +600 to +900 tokens (CLI/WS only;
~+200 tokens on chat channels).

**Verification.**

- `uv run pytest tests/agent/test_context*.py tests/agent/test_runner.py -v`
- Snapshot test: render `identity.md` for `channel=cli`, `channel=telegram`,
  `channel=email` — assert the parallelism block only appears in the
  CLI render.
- Manual smoke: `pythinker agent` → ask for a code edit → confirm the
  agent uses `edit_file` rather than printing the diff inline.

**Risk band.** Low. Additive only. If something regresses, revert the
single `{% include %}` line.

---

### Phase 2 — Subagent role split: `coder` / `explore` / `plan`

**Goal.** Replace the single `subagent_system.md` with three role
variants, each with a tailored prompt and a tool-allow-list. This is
the largest *coding-ability* lever — explore and plan agents are the
model's de-facto "context-gathering" and "design" modes, and Pythinker
currently has neither.

**Files touched.**

- New: `pythinker/templates/agent/subagent_coder.md`
- New: `pythinker/templates/agent/subagent_explore.md`
- New: `pythinker/templates/agent/subagent_plan.md`
- Edit: `pythinker/templates/agent/subagent_system.md` — keep as the
  shared header (workspace + time + skills + untrusted-content
  snippet), add `{% include role_template %}` at the bottom.
- Edit: `pythinker/agent/subagent.py` —
  - `SubagentManager.spawn(...)` accepts `role` and threads it into
    `_run_subagent`.
  - `_run_subagent` builds a `ToolRegistry` whose contents depend on
    `role` (explore: read-only set; plan: read-only set minus exec;
    coder: current full set).
  - `_build_subagent_prompt` accepts `role` and renders
    `subagent_system.md` with `role_template = f"agent/subagent_{role}.md"`.
- Edit: `pythinker/agent/tools/spawn.py` — schema gains a
  `role: Literal["coder","explore","plan"] = "coder"` parameter; the
  description gains "thoroughness" (quick / medium / thorough) and
  "when not to use" guidance.

**Role prompt skeletons.**

- `subagent_coder.md` — ~20 lines: "you are running as a subagent",
  parent-agent-as-caller framing, "do not call user-facing tools",
  "summarize technical findings to the parent". Tightens the
  parent-agent contract over Pythinker's current generic prompt.
- `subagent_explore.md` — ~30 lines: read-only specialty, glob/grep/
  read_file/git-readonly only, parallelism encouraged, thoroughness
  levels interpreted from the prompt.
- `subagent_plan.md` — ~25 lines: three-state output (known /
  unknown / plan), recommend explore for missing context, no exec /
  write_file / edit_file.

**Tool gating (in `_run_subagent`).**

| Role | Tools registered |
|---|---|
| `coder` (default) | Current set: read/write/edit/list/glob/grep + exec + web (existing behavior) |
| `explore` | read_file, list_dir, glob, grep, web_search, web_fetch — **no** write_file, edit_file, exec |
| `plan` | read_file, list_dir, glob, grep, web_search, web_fetch — **no** exec, write_file, edit_file |

**Skill interaction.** `SkillsLoader` runs per-spawn and may surface skills
whose `allowed-tools` declare write/exec. Role gating wins: a non-coder
subagent must filter out skills whose `allowed-tools` are not a subset of
the role's registered tool set, rather than fail at first use. Implement
the filter in `_run_subagent` next to the `ToolRegistry` build, not inside
`SkillsLoader` — keeps the loader role-agnostic.

**Estimated delta.** +75 LOC of templates, +30 LOC in `subagent.py`,
+10 LOC in `spawn.py`. Net **+115 LOC**, +900 to +1,200 tokens *only*
when a non-coder role is used (default coder behavior is unchanged).

**Verification.**

- New: `tests/agent/test_subagent_roles.py` — three cases: spawn with
  `role="explore"` must reject write tool calls; `role="plan"` must
  reject exec; `role="coder"` must keep existing behavior.
- Existing: `uv run pytest tests/agent/test_subagent*.py -v` must
  pass with no changes (coder role is behavior-identical).
- Manual smoke: spawn an explore subagent that tries to `edit_file` →
  expect tool-error path.

**Risk band.** Medium. Default behavior unchanged (role defaults to
`coder`), but the spawn schema gains a parameter — old callers
passing `spawn(task=...)` keep working.

---

### Phase 3 — Compaction prompt upgrade

**Goal.** Replace the 13-line bullet-extractor with a structured
compaction prompt that retains code-state, error/solution pairs, and
the current task. This is what makes long coding sessions survive
compaction without losing the working file context.

**Files touched.**

- Edit: `pythinker/templates/agent/consolidator_archive.md` — replace
  with a structured prompt of ~50 lines: priorities, compression
  rules, and `<current_focus>` / `<environment>` / `<completed_tasks>`
  / `<active_issues>` / `<code_state>` / `<important_context>`
  skeleton.
- No code changes — `Consolidator` already passes whatever the
  template produces to the LLM and stores the response.

**Caveat.** Pythinker's `Consolidator` writes its output into
`MEMORY.md`, not back into the live message history. Keep the output
flat (no nested `<file>` blocks) because flat sections render better
in `MEMORY.md` when it's reloaded as bootstrap content on the next
turn. Don't rely on the next turn re-parsing structured tags — they
exist for the LLM that wrote the section, not for downstream code.

**Estimated delta.** +40 LOC in the template, 0 LOC of code. Tokens
roughly double the consolidation prompt (still small relative to
turn-level prompt).

**Verification.**

- `uv run pytest tests/agent/test_memory.py -v` — exercises the
  consolidator path. Existing tests should still pass; add one new
  assertion that the structured tags appear in the consolidator
  output.

**Risk band.** Low. The change is a single template edit; if the
LLM ignores the structure, output is still bullets.

---

### Phase 4 — `/init` AGENTS.md generator

**Goal.** Add a single slash-command that walks the user's current
project root and produces a project-tuned AGENTS.md. The lever for
"coding ability in unfamiliar repos" is large because every
subsequent turn benefits from a curated AGENTS.md.

**Files touched.**

- New: `pythinker/templates/agent/init_agents_md.md` — ~25 lines:
  "explore → identify config files → identify stack → write
  AGENTS.md" prompt with the AGENTS.md schema this project already
  uses (Project overview / Build & test / Code style / Testing /
  Security).
- Edit: `pythinker/agent/loop.py` slash-command dispatcher — add
  `/init` (~20 LOC). It loads the template, sends it as a user
  message, and returns; the agent's normal tool-use flow does the
  rest. **No checkpoint-key changes** (`_RUNTIME_CHECKPOINT_KEY`,
  `_PENDING_USER_TURN_KEY`) — `/init` is a synchronous user-message
  injection, not a new turn-state, per the cross-plan invariant in
  `2026-05-04-simplification-alignment.md`.

**Estimated delta.** +25 LOC template, +20 LOC code.

**Verification.**

- New: `tests/command/test_init_command.py` — assert `/init` injects
  the prompt and the next turn calls `read_file` / `glob` first.

**Risk band.** Low. Opt-in slash command. No existing path changes.

---

### Phase 5 — Plan-mode + AFK-mode dynamic injection (deferred)

**Goal.** Adopt a periodic-reminder pattern so plan-mode discipline
survives long sessions and AFK-mode (no-human-on-channel) can
auto-disable AskUser-style flows.

**Conditional ship.** Pythinker has no `EnterPlanMode` /
`ExitPlanMode` tools today. So this phase **only** ships if Phase 4
lands and we add a plan-file convention (e.g.,
`${workspace}/.pythinker/plan.md`).

**Files touched.**

- New: `pythinker/agent/dynamic_injection.py` — minimal interface
  (~80 LOC) for `DynamicInjection` / `DynamicInjectionProvider`.
- New: `pythinker/agent/dynamic_injections/plan_mode.py` — periodic
  injector with throttle (full vs sparse vs reentry variants).
- New: `pythinker/agent/dynamic_injections/afk_mode.py` — AFK
  semantics in Pythinker = "no human is listening on this channel"
  (heartbeat / cron / scheduled-message turns). Useful even without
  plan mode.
- Edit: `pythinker/agent/runner.py` — single hook point in
  `_prepare_turn_messages` (or equivalent) that calls
  `provider.get_injections(history, soul)` and prepends results to
  the next user message. ~15 LOC.

**Estimated delta.** +200 LOC (mostly tests), +1 hook in the runner.

**Verification.**

- New: `tests/agent/test_dynamic_injection.py` — coverage for
  throttle cadence, full-vs-sparse cycling, AFK re-arm on
  compaction.

**Risk band.** Medium-High. Touches the hot-path runner. **Defer
unless Phase 4 ships and there is a real plan-mode user story.**

---

## 5. What we are not adopting

| Pattern | Reason to skip |
|---|---|
| Per-tool full-system-prompt re-substitution layer | Pythinker already uses Jinja with named kwargs in `render_template` — no new substitution layer needed. |
| Persona-only agent variants | Not a coding lever. |
| Per-OS Windows-only file-tool preference banner | Already covered by `pythinker/templates/agent/platform_policy.md`. |
| Skill-creator schema | Pythinker has its own, validated by `pythinker/skills/skill-creator/scripts/quick_validate.py`. Different schema (`always` / `license` / `allowed-tools`). |
| Mermaid/D2 prompt-flow skill type | Speculative feature, no Pythinker user story. |
| Wire-event types | Runtime-bridge concern, not prompt layer. |

---

## 6. Build sequence + sequencing rationale

```
Phase 1 (root directives)        ← lands first; pure additive; biggest impact
   │
   ▼
Phase 2 (subagent role split)    ← unlocks fast read-only exploration
   │
   ▼
Phase 3 (compaction)             ← independent of 1/2 but cheap; ride along
   │
   ▼
Phase 4 (/init)                  ← optional; ships when there's user demand
   │
   ▼
Phase 5 (dynamic injections)     ← conditional on plan-mode tool feature
```

Each phase is independently revertable with a single-file diff (Phase
2 touches three files, but the new templates are inert without the
schema-parameter change in `spawn.py`).

---

## 7. Token-budget accounting

| Surface | Today | After Phase 1 | After Phase 1+2 | After 1+2+3 |
|---|---:|---:|---:|---:|
| Root system prompt (CLI) | ~2.5 k | ~3.4 k | ~3.4 k | ~3.4 k |
| Root system prompt (Telegram) | ~2.3 k | ~2.5 k | ~2.5 k | ~2.5 k |
| Subagent prompt (coder) | ~0.3 k | ~0.3 k | ~0.4 k | ~0.4 k |
| Subagent prompt (explore) | n/a | n/a | ~0.6 k | ~0.6 k |
| Subagent prompt (plan) | n/a | n/a | ~0.5 k | ~0.5 k |
| Compaction prompt | ~0.2 k | ~0.2 k | ~0.2 k | ~0.6 k |
| **Net delta vs today** | — | **+0.7 k** | **+1.8 k** | **+2.2 k** |

Net delta sits inside the +1.2 k root + +0.6 k per subagent budget
defined in § 3.5.

---

## 8. Verification ladder (full plan)

- `uv run ruff check pythinker --select F401,F841` — clean.
- `uv run ruff check pythinker` — clean.
- `uv run pytest tests/agent/ tests/command/ -v` — green.
- `uv run pytest tests/test_agents_skills.py -v` — green (Phase 4
  adds a new template; the skill-validator guard must keep passing).
- Snapshot test: render the root system prompt for the cartesian
  product `{cli, telegram, email, whatsapp} × {empty workspace,
  populated workspace}` — assert (a) channel-conditional blocks
  render correctly and (b) total tokens stay inside the budget in
  § 7.
- Manual smoke matrix:
  1. `pythinker agent` → ask for a code edit → confirm `edit_file` is
     used, not text description.
  2. From the CLI agent, `spawn(role="explore", task="how does the
     consolidator decide when to compact?")` → confirm the explore
     subagent doesn't try to write a file.
  3. From the CLI agent, `spawn(role="plan", task="add a /yolo
     command")` → confirm the plan subagent returns the three-state
     known/unknown/plan response.
- (Phase 4 only) From a fresh repo, `/init` → AGENTS.md is created
  with the project's actual structure, not a generic template.

---

## 9. Out of scope — log here, do not fix in this work

- `_build_subagent_prompt` is a static method that re-instantiates
  `SkillsLoader` on every spawn — minor inefficiency, unrelated to
  coding ability.
- `SOUL.md` template currently overlaps with `identity.md` in tone
  ("Solve by doing"). Could be merged later; not part of this plan.
- The `dream_phase1.md` / `dream_phase2.md` / `evaluator.md` prompts
  are dream-mode-specific and have their own simplification track
  (see `2026-05-04-simplification-alignment.md`).
- The PDF deliverable contract in `identity.md` is verbose and
  channel-specific; orthogonal to coding ability.
- Per-tool description fields under `pythinker/agent/tools/` (shell,
  read_file, write_file, edit_file, glob, grep, web_*, todo,
  ask_user) — kept as-is in this plan. Tightening individual tool
  descriptions is a separate, mechanical pass that does not depend
  on any phase here.

---

## 10. Open questions for the user

1. **Phase scope.** Should the implementation pass do **Phase 1
   only** (smallest, safest), **Phases 1+2** (biggest coding-ability
   impact), or all of 1–4? Phase 5 is recommended deferred regardless.
2. **Branch target.** Pythinker uses two-branch model: `main` =
   stable, `dev` = experimental. Adding subagent roles is a feature
   → likely `dev`. Phase 1's prompt additions could land on either
   branch.
3. **Plan-mode user story.** Is there a real product need for a
   Pythinker `/plan` command + plan-file convention? If no, skip
   Phase 5 entirely.
4. **Channel coverage of coding directives.** The plan currently
   shows the parallelism + system-reminder paragraphs only on `cli` /
   `websocket`. Do we want them on Slack/Discord too? (Where
   developers sometimes ask for code edits via DM.)

A "yes / no / yes / yes" set of answers makes the next plan a
mechanical translation of phases 1–4 into a file-by-file
implementation plan. Anything else, ask back.
