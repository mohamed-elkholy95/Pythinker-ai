## `.agents/` — Maintainer Workflows for Coding Agents

Versioned, repo-scoped workflows for **AI coding agents working on Pythinker**.
This directory is the operational counterpart to `AGENTS.md` (telegraph-style
ruleset) and `CLAUDE.md` (kickoff manual): when an agent is mid-task, these
skills give it a focused playbook for one common maintainer surface.

### What lives here

```
.agents/
├── README.md                              # this file
├── scripts/
│   └── validate_skills.py                 # CI/local lint (calls canonical validator)
└── skills/
    ├── pythinker-browser/SKILL.md         # browser tool modes, provisioning, SSRF triage
    ├── pythinker-debug/SKILL.md           # bus / provider / channel / sandbox triage
    ├── pythinker-release/SKILL.md         # PyPI release + Trusted Publishing
    ├── pythinker-channel-test/SKILL.md    # add or modify a channel adapter
    └── pythinker-provider-test/SKILL.md   # add or modify an LLM provider
```

### What this is NOT

- **Not loaded by Pythinker's runtime.** `pythinker/agent/skills.py` only
  resolves `<workspace>/skills` and the bundled `pythinker/skills/` tree.
  Nothing under `.agents/` is exposed to a running Pythinker agent.
- **Not for end users.** Runtime-facing skills (the ones an end user could
  call from their workspace) belong in `pythinker/skills/`.
- **Not gitignored.** These skills are versioned with the repo so every
  agent on every clone gets the same playbook.

### Skill format

Each skill follows the canonical Pythinker SKILL.md spec — same one
`pythinker/skills/skill-creator/scripts/quick_validate.py` enforces:

- Frontmatter is YAML with allowed keys: `name`, `description`,
  `metadata`, `always`, `license`, `allowed-tools`.
- `name` is hyphen-case, ≤64 chars, matches the directory name.
- `description` is non-empty, ≤1024 chars, no angle brackets, no TODO
  placeholder text.
- Folder root may contain `SKILL.md` plus only the `scripts/`,
  `references/`, and `assets/` directories (no symlinks).
- Pythinker-specific metadata lives under `metadata.pythinker.*`
  (`emoji`, `os`, `requires.bins`, `requires.env`, `install`).

### Adding a new maintainer skill

1. Create `.agents/skills/<name>/SKILL.md` with valid frontmatter.
2. Cite hot-path file refs as `pythinker/path/file.py:LINE` (no absolute
   paths, no `~/`).
3. Run the validator:
   ```bash
   uv run python .agents/scripts/validate_skills.py
   ```
4. The CI guard at `tests/test_agents_skills.py` will fail fast on any
   regression.

### When to add one

Add a new skill when an operational task in this repo is:

- Multi-step and easy to get wrong (release pipeline, schema migration).
- Spread across files an agent would have to grep for (channel/provider
  add, sandbox tuning, secret rotation).
- A recurring triage path (bus stalls, sandbox failures, CI-only flakes).

Routine bug fixes, refactors, and one-shot doc edits do not need a
skill — they're covered by `AGENTS.md` and the in-repo `tasks/`
workflow.

### Boundaries

- **Maintainer-only.** Don't reference `.agents/` from any
  runtime/published surface (CLI, docs ship dirs, wheel `force-include`,
  README, public docs).
- **Mirror reality.** When an internal constant or module name moves,
  update the citing skill in the same PR. The validator catches shape
  errors but cannot catch stale facts — `tests/test_agents_skills.py`
  asserts the file refs still resolve.
- **Stay surgical.** Skills are playbooks, not encyclopaedias. If a
  section grows past one screen, split it into a `references/` file the
  agent loads on demand.
