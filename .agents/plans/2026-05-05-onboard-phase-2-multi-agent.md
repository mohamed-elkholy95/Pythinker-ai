# Onboard Phase 2 — Multi-Agent Layout

> **Maintainer-only.** Lives under `.agents/plans/` per `.agents/README.md` § Boundaries.
>
> **Status:** Draft. Successor to `tasks/todo.md` Phase 1 (which is shipped). Phase 2 was the deferred follow-up: support `~/.pythinker/agents/<id>/` so a single host can run multiple distinct Pythinker agents with their own config, workspace, and (optionally) credentials.
>
> **Date:** 2026-05-05
>
> **Scope.** Only the persistent-storage layout and the CLI surface that picks which agent to load. Runtime policy (`pythinker/runtime/manifest.py` `AgentManifest`, `agent_id` on `RequestContext`) is already first-class — this plan does not redesign it.

---

## 0. Why this matters

The runtime already has an `agent_id` concept (see `pythinker/runtime/context.py:67` — `agent_id: str = "default"`) and a manifest registry that scopes tool permissions per agent. What's missing is the **operator-facing surface**: today every `pythinker` invocation reads `~/.pythinker/config.json` and writes to a single workspace. To run two distinct agents on the same host (e.g. a coding assistant and a research agent), the user must juggle `--config` and `--workspace` flags by hand and reuse OAuth tokens that may not match.

Phase 1 of `tasks/todo.md` deliberately deferred this — it stays out-of-scope (line 213): _"No `~/.pythinker/agents/<id>/` layout, no per-agent auth-profiles store. Pythinker stays single-config."_ This plan re-opens that decision.

## 1. End-state layout

```
~/.pythinker/
├── config.json                    # legacy single-agent config (back-compat)
├── current-agent                  # plain-text file: id of the currently active agent
└── agents/
    ├── default/
    │   ├── config.json            # mirrors today's ~/.pythinker/config.json
    │   ├── workspace/             # MEMORY.md, SOUL.md, USER.md, history.jsonl, skills/
    │   └── manifest.json          # OPTIONAL — the existing AgentManifest schema
    ├── research/
    │   ├── config.json
    │   ├── workspace/
    │   └── manifest.json
    └── coding/
        ├── config.json
        ├── workspace/
        └── manifest.json
```

**Resolution order** (when no `--agent-id` flag is supplied):

1. `$PYTHINKER_AGENT_ID` env var.
2. `~/.pythinker/current-agent` (one line, the id).
3. `default`.

If `~/.pythinker/agents/<id>/config.json` doesn't exist, fall back to `~/.pythinker/config.json` (keeps single-config users working unchanged).

**OAuth tokens stay shared.** Per-agent token stores would multiply the OAuth dance for no real isolation gain on a single-user host. The `~/.local/share/oauth-cli-kit/auth/` and `~/.local/share/pythinker/auth/` paths from `pythinker/cli/onboard_views/reset.py:51-57` remain global. Documented as a deliberate constraint, not a TODO.

---

## 2. Phasing — three independently-shippable PRs

Each PR can land separately on `dev`. Only PR-3 changes user behavior; PR-1 and PR-2 are additive.

### PR-1 — Path resolution + agent-id plumbing (additive, low risk)

**Goal.** Introduce the layout and resolution order, but keep `~/.pythinker/config.json` as the canonical source until PR-3.

**Files.**

- Edit: `pythinker/config/paths.py` —
  - Add `current_agent_id() -> str` (reads env → `current-agent` → `"default"`).
  - Add `agent_dir(agent_id: str) -> Path` returning `~/.pythinker/agents/<id>/`.
  - Add `agent_config_path(agent_id: str) -> Path` returning `<agent_dir>/config.json`, falling back to `~/.pythinker/config.json` if the agent dir doesn't exist.
- Edit: `pythinker/config/loader.py` — `get_config_path()` consults `current_agent_id()` first when no override is set.
- Edit: every Typer command in `pythinker/cli/commands.py` that already takes `--config` — add a `--agent-id` flag that sets `set_config_path(agent_config_path(agent_id))` before delegating.
- New: `tests/config/test_agent_paths.py` — resolution order + fallback to legacy single config.

**Net delta.** ~+80 LOC, ~+30 LOC tests.

**Risk.** Low. Default behavior unchanged (resolves to "default" → falls back to legacy path).

**Acceptance gate.**

- [ ] `pythinker doctor` works unchanged on a single-config install.
- [ ] `PYTHINKER_AGENT_ID=research pythinker doctor` reads `~/.pythinker/agents/research/config.json`.
- [ ] Full pytest green.

### PR-2 — Agent management subcommand

**Goal.** Add `pythinker agents {list, create, switch, delete}` so the user can manage the layout without manual file ops.

**Files.**

- New: `pythinker/cli/agents.py` — Typer sub-app.
  - `agents list` — table of `id | model | enabled-tools | last-used`.
  - `agents create <id>` — scaffolds `~/.pythinker/agents/<id>/{config.json, workspace/}`. Optional `--from <other-id>` to copy an existing agent's config as a starting point.
  - `agents switch <id>` — writes `~/.pythinker/current-agent`. Refuses to switch to an id that doesn't have a config.
  - `agents delete <id>` — refuses if `id` is the currently active one or `default`. Requires `--confirm <id>` to actually wipe.
- Edit: `pythinker/cli/commands.py` — `app.add_typer(agents_app, name="agents")`.
- Edit: `docs/cli-reference.md` — new `## pythinker agents` section.
- New: `tests/cli/test_agents_cmd.py` — list / create / switch / delete behaviors.

**Net delta.** ~+200 LOC, ~+150 LOC tests.

**Risk.** Low-medium. Pure additive. Delete path needs a confirm-token guard; mirrors the pattern in `pythinker cleanup run --confirm reset`.

**Acceptance gate.**

- [ ] Round-trip: `agents create research → switch research → doctor` reads the new agent's config.
- [ ] `agents delete default` is refused unconditionally.
- [ ] `agents delete <id>` requires `--confirm <id>` and refuses without it.
- [ ] Full pytest green.

### PR-3 — Wizard multi-agent flow

**Goal.** Make `pythinker onboard` aware of the multi-agent layout so a fresh user is asked which agent to configure (if any agents exist) and a returning user can add a second agent without flag gymnastics.

**Files.**

- Edit: `pythinker/cli/onboard.py` — new step `_step_agent_id` that runs first when `agents/` exists. Three options: `Use <current>`, `Pick a different agent`, `Create a new agent`. The chosen agent id flows into `_WizardContext.agent_id` and overrides config-path resolution for the rest of the wizard.
- Edit: `pythinker/cli/onboard_steps/existing_config.py` — when an agent dir already has a config, surface the existing `_prompt_configured_action` helper (Use existing / Edit / Reset).
- Edit: `pythinker/cli/onboard_views/summary.py` — pre-save diff renders agent id in the panel title.
- New: `tests/cli/test_onboard_multi_agent.py` — three flows: zero-agents host, one-agent host, multi-agent host.

**Net delta.** ~+150 LOC, ~+200 LOC tests.

**Risk.** Medium. Touches the wizard orchestrator. Non-multi-agent users (single-config installs) must see no behavior change — the new step skips itself when `~/.pythinker/agents/` doesn't exist.

**Acceptance gate.**

- [ ] Single-config install: `pythinker onboard` is byte-identical to today's flow (no new step rendered).
- [ ] Multi-agent install: wizard asks for agent-id at step 0; a wrong answer is recoverable via `[Back]`.
- [ ] Pre-save diff title includes the chosen agent id.

---

## 3. Out of scope

- **Per-agent OAuth tokens.** Shared at `~/.local/share/`. (See §1.)
- **Per-agent `manifest.json` write path.** PR-1 reads manifests if present; this plan does not add a "create manifest" UI. Operators continue to write JSON by hand for policy scoping.
- **Cross-agent message routing.** The bus stays per-process; one `pythinker gateway` instance still serves one agent at a time.
- **Plugin SDK / install catalog.** Same Phase-1-deferred reason as `tasks/todo.md` line 215.
- **Live model catalog fetch.** Out of scope per `tasks/todo.md` line 219.

---

## 4. Migration

For the small number of users with an existing `~/.pythinker/config.json`, the resolution fallback in PR-1 means **zero migration is required**. They keep working on the legacy path until they explicitly run `pythinker agents create default --from-legacy` (PR-2 helper, optional).

No automatic migration on first run. Surprise file moves are exactly the kind of thing that wakes a maintainer up at 3am — keep it explicit.

---

## 5. Verification

Land each PR with:

```bash
uv run ruff check pythinker --select F401,F841
uv run ruff check pythinker
uv run pytest tests/                       # full suite
echo "/help\nexit" | uv run pythinker agent
uv run pythinker doctor
```

Plus, after PR-3:

- Manual: fresh `~/.pythinker/` → `pythinker agents create coding && pythinker agents create research && pythinker agents switch coding && pythinker doctor` reads the right config.

---

## 6. Approval gate

- [ ] Maintainer: approve PR-1 scope (paths + plumbing).
- [ ] Maintainer: confirm legacy `~/.pythinker/config.json` continues to work without migration.
- [ ] Maintainer: confirm OAuth tokens stay shared (no per-agent token store).
- [ ] Maintainer: approve PR-2 scope (agents subcommand surface) once PR-1 is in.
- [ ] Maintainer: approve PR-3 scope (wizard flow) once PR-1 + PR-2 are in.

Once PR-1 box is ticked, an implementer may begin PR-1.

---

## 7. Errata changelog

| Pass | Date | What changed |
|---|---|---|
| 0 | 2026-05-05 | Initial cut. Successor to `tasks/todo.md` Phase 1 §"Out of scope" line 213. Three-PR phasing. |
