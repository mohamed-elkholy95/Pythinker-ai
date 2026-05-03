# Contributing to Pythinker

Thank you for being here.

Pythinker is built with a simple belief: good tools should feel calm, clear, and humane.
We care deeply about useful features, but we also believe in achieving more with less:
solutions should be powerful without becoming heavy, and ambitious without becoming
needlessly complicated.

This guide is not only about how to open a PR. It is also about how we hope to build
software together: with care, clarity, and respect for the next person reading the code.

## Maintainers

| Maintainer | Focus |
|------------|-------|
| [@mohamed-elkholy95](https://github.com/mohamed-elkholy95) | Project lead, `main` + `dev` branches |

## Branching Strategy

We use a two-branch model to balance stability and exploration:

| Branch | Purpose | Stability |
|--------|---------|-----------|
| `main` | Stable releases | Production-ready |
| `dev` | Experimental features | May have bugs or breaking changes |

### Which Branch Should I Target?

**Target `dev` if your PR includes:**

- New features or functionality
- Refactoring that may affect existing behavior
- Changes to APIs or configuration

**Target `main` if your PR includes:**

- Bug fixes with no behavior changes
- Documentation improvements
- Minor tweaks that don't affect functionality

**When in doubt, target `dev`.** It is easier to move a stable idea from `dev` to `main`
than to undo a risky change after it lands in the stable branch.

### How Does `dev` Get Merged to `main`?

We don't merge the entire `dev` branch. Instead, stable features are **cherry-picked**
from `dev` into individual PRs targeting `main`:

```
dev  ──┬── feature A (stable) ──► PR ──► main
       ├── feature B (testing)
       └── feature C (stable) ──► PR ──► main
```

This happens on an as-needed cadence — when a feature has settled and is ready for a
stable release.

### Quick Summary

| Your Change | Target Branch |
|-------------|---------------|
| New feature | `dev` |
| Bug fix | `main` |
| Documentation | `main` |
| Refactoring | `dev` |
| Unsure | `dev` |

## Development Setup

Keep setup boring and reliable. The goal is to get you into the code quickly.

Pythinker is a Python package (`pythinker/`) plus a Vite/React web UI (`webui/`) and a
small Node bridge for WhatsApp (`bridge/`). You only need to set up the parts you plan to
touch.

### Python (core package)

We use [`uv`](https://docs.astral.sh/uv/) in CI because it's fast and deterministic. Plain
`pip` works too.

```bash
# Clone the repository
git clone git@github.com:mohamed-elkholy95/Pythinker-ai.git
cd Pythinker-ai

# Recommended: uv
uv sync --all-extras

# Alternative: pip + venv
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run tests
uv run pytest tests/
# or: pytest tests/

# Lint
uv run ruff check pythinker
# or: ruff check pythinker

# Format
uv run ruff format pythinker
# or: ruff format pythinker
```

`pyproject.toml` is the source of truth for dependencies, lint rules, and test
configuration. If you're unsure which extras to install, `--all-extras` / `[dev]` covers
the full development surface.

### Web UI

The WebUI lives in `webui/` and uses [Bun](https://bun.sh/) (lockfile: `bun.lock`). `npm`
also works, but please do not commit npm or pnpm lockfiles alongside `bun.lock`.

```bash
cd webui
bun install
bun run dev     # local dev server
bun run test    # vitest
bun run build   # production bundle
```

### Node bridge (WhatsApp)

The `bridge/` directory is only needed if you're working on the WhatsApp channel.

```bash
cd bridge
npm install
npm run build
```

## Code Style

We care about more than passing lint. We want Pythinker to stay small, calm, and readable.

When contributing, please aim for code that feels:

- **Simple** — prefer the smallest change that solves the real problem.
- **Clear** — optimize for the next reader, not for cleverness.
- **Decoupled** — keep boundaries clean and avoid unnecessary new abstractions.
- **Honest** — do not hide complexity, but do not create extra complexity either.
- **Durable** — choose solutions that are easy to maintain, test, and extend.

### Python

- **Line length**: 100 characters (enforced by `ruff`; `E501` is ignored so long strings
  and comments don't block you, but treat 100 as a target).
- **Target**: Python 3.11+. CI tests 3.11, 3.12, 3.13, and 3.14 on Ubuntu and Windows.
- **Linting**: `ruff` with rule groups `E, F, I, N, W` (see `[tool.ruff.lint]` in
  `pyproject.toml`). CI is strictest about `F401` (unused imports) and `F841` (unused
  variables) — please clean those before opening a PR.
- **Typing**: Python is dynamically typed, but we prefer annotations on public functions,
  dataclasses, and anything on the message bus / tool registry boundary.
- **Async**: the codebase is `asyncio`-native. `pytest` is configured with
  `asyncio_mode = "auto"`, so `async def test_…` functions run as coroutines automatically.
- **Logging**: use `loguru` (`from loguru import logger`), not the stdlib `logging` module.
- **Formatting**: `ruff format` is available but not enforced in CI. Match the surrounding
  file when in doubt.

### TypeScript / React (webui)

- Uses Vite + TypeScript 5.7 + Tailwind + Radix UI. Keep components small, typed, and
  colocated with the feature they serve.
- Tests use Vitest + Testing Library. Put tests in `webui/src/tests/` or next to the unit.
- Match the existing import style and file naming. Prefer functional components and hooks.

### Node (bridge)

- TypeScript, compiled via `tsc`. Keep the bridge as thin as possible — it's a relay, not
  a second runtime.

### In practice

- Prefer readable code over magical code.
- Prefer focused patches over broad rewrites.
- If a new abstraction is introduced, it should clearly reduce complexity rather than
  move it around.
- Do not add drive-by reformatting to unrelated files; it makes review harder.

## Tests

- Python tests live under `tests/`, organized by subsystem (`tests/agent/`,
  `tests/channels/`, `tests/providers/`, etc.). New behavior needs a test; bug fixes
  should include a test that fails before the fix and passes after.
- Web UI tests live under `webui/src/tests/` and run with `bun run test`.
- CI runs `ruff check pythinker --select F401,F841` and `pytest tests/` across the Python
  matrix. Please make sure both pass locally before opening a PR.

## Commits and Pull Requests

- **Commit messages**: imperative mood, short subject (≤72 characters). The body explains
  *why*, not *what* — the diff shows *what*.
- **One logical change per PR**. If your branch grew into several changes, split it.
- **PR description**: explain the motivation, summarize the change, and note anything a
  reviewer should double-check. Link related issues.
- **Target the right branch** (see the table above).
- **Keep history honest**. Rebase or squash if that produces a cleaner story, but never
  force-push over someone else's work.

## Security

If you find a vulnerability, **please do not open a public issue**. Follow the process in
[`SECURITY.md`](./SECURITY.md) — private disclosure first, so we can fix it before it's
exploited.

## Questions?

If you have questions, ideas, or half-formed insights, you are warmly welcome here.

- Open an [issue](https://github.com/mohamed-elkholy95/Pythinker-ai/issues) for bugs or
  feature requests.
- Start a [discussion](https://github.com/mohamed-elkholy95/Pythinker-ai/discussions) for
  questions, design conversations, and show-and-tell.

Thank you for spending your time and care on Pythinker. We would love for more people to
participate in this community, and we genuinely welcome contributions of all sizes.
