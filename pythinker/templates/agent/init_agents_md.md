Walk this project's root directory and produce a tuned `AGENTS.md` at the repo root. The file should make every subsequent agent turn cheaper by capturing what's not derivable in 30 seconds.

## Workflow

1. **Explore.** Use `glob` and `grep` in parallel to find:
   - Build / dependency files: `pyproject.toml`, `setup.py`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`, `Pipfile`, `requirements*.txt`.
   - Test config: `pytest.ini`, `tox.ini`, `vitest.config.*`, `jest.config.*`, the `[tool.pytest]` block in `pyproject.toml`.
   - Lint / format: `.ruff.toml`, `.eslintrc*`, `.prettierrc*`, `.editorconfig`, the `[tool.ruff]` block.
   - CI: `.github/workflows/*.yml`, `.gitlab-ci.yml`, `Jenkinsfile`.
   - Top-level dirs: `read_file` no — `list_dir .` and pick the 5–10 names that look load-bearing (`src/`, `lib/`, `app/`, `services/`, `tests/`, `docs/`, `web/`).
   - Docs the agent should respect: existing `AGENTS.md` (do not overwrite without diffing), `CONTRIBUTING.md`, `CLAUDE.md`, `SECURITY.md`, `README.md`.

2. **Identify the stack.** From the manifests, name the language(s), package manager(s), test runner(s), lint tool(s). One sentence per axis.

3. **Read selectively.** Open the top of `README.md` and the top of one or two key source files (the largest module, or the entry point named in `[project.scripts]` / `package.json` `bin`). Don't read everything — you're skimming for the architectural spine.

4. **Write `AGENTS.md`** with `write_file` using this schema. Skip a section entirely if it would be empty — do not emit `(none)` placeholders.

```
# AGENTS.md

## Project overview
<2–3 sentences: what this project does, who uses it, the runtime spine
in one named module if there is one (e.g. "MessageBus → AgentLoop").>

## Build & test
- Install: <one command>
- Run tests: <one command>
- Lint: <one command>
- Format: <one command, if separate>
- Type-check: <one command, if applicable>

## Code style
- Language(s): <Python 3.X, TypeScript Y, ...>
- Line length: <number, if enforced>
- Formatter rules: <key non-defaults — e.g. "double quotes", "trailing
  commas in multi-line">
- Import order: <if convention exists>
- Naming: <conventions worth flagging — e.g. "private helpers prefixed
  with _", "no abbreviations in public API names">

## Testing
- Framework: <pytest, vitest, jest, go test, ...>
- Test layout: <where tests live; do they mirror source layout?>
- Async pattern: <e.g. "asyncio_mode = auto in pyproject.toml">
- Coverage tool: <if used>

## Security / sandbox / boundaries
<Anything an agent can break by accident: secrets in env vars, sandbox
constraints, "do not commit X", "do not run Y without Z". Pull from
SECURITY.md / CONTRIBUTING.md if present.>

## Common file locations
| Surface | Path |
|---|---|
| <e.g. Entry point> | <e.g. src/main.py> |
| <e.g. Tests> | <e.g. tests/> |
| <Up to 5 rows — only the ones an agent needs to find fast.> | |
```

## Ground rules

- **Do not invent.** If you can't determine a section from the actual files, omit it. A short, accurate `AGENTS.md` beats a long speculative one.
- **Do not duplicate `README.md`.** Cross-reference it ("see README §Quickstart for end-user setup") rather than copying the install instructions verbatim. `AGENTS.md` is for agents, not users.
- **Do not overwrite an existing `AGENTS.md` without diffing.** If one exists, `read_file` it first, summarize what would change, and ask before clobbering.
- **Be terse.** Every line should change a future turn's behavior. If removing a line wouldn't confuse a future agent, drop it.
- Final reply: report the path you wrote, the section count, and any open questions you couldn't resolve from the files alone.
