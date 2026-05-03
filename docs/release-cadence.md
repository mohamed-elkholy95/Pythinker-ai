# Release Cadence

This document defines how Pythinker is released over time. It is the
**date-driven** layer on top of the event-driven mechanics in
[`.agents/skills/pythinker-release/SKILL.md`](../.agents/skills/pythinker-release/SKILL.md).

The skill answers *how to ship a release*. This document answers *when, why,
and against what schedule* — so contributors and downstream users have a
predictable release horizon and a clear contract for what each tag means.

## Goals

1. **Predictability for users.** A user installing `pythinker-ai` should
   know roughly when the next bug-fix release lands and when a minor
   release with new behaviour will arrive.
2. **Predictability for contributors.** A maintainer cutting a release
   should not need to invent the process each time. Every release follows
   the same checklist, the same gates, and the same calendar slot.
3. **Quality at the gate.** Every published version is provably green:
   ruff clean, full pytest matrix passing, install-smoke verified on
   ubuntu/macos/windows, and the post-publish PyPI smoke succeeded.
4. **Reversibility.** A bad release is recoverable in minutes, not days.

## Versioning policy

Pythinker follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

| Bump | Trigger | Example |
|------|---------|---------|
| **PATCH** (`X.Y.Z+1`) | Bug fix, doc fix, packaging fix, internal refactor with no user-visible behaviour change | 2.0.1 → 2.0.2 |
| **MINOR** (`X.Y+1.0`) | New behaviour, new channel/provider/tool, new flag — backwards-compatible | 2.0.x → 2.1.0 |
| **MAJOR** (`X+1.0.0`) | Breaking change to public CLI, config schema, message-bus events, or Python SDK surface | 2.x → 3.0 |

Pre-releases (RCs) use the canonical SemVer suffix: `X.Y.Z-rc.N` (e.g.
`2.1.0-rc.1`). They publish to PyPI but `pip install pythinker-ai` will
not pick them up unless `--pre` is passed.

Build metadata (`+...`) is reserved for special cases (e.g. rebuilds of
the same source) and must never appear on a tagged release.

## Project phase: active development

Pythinker is in **active development**. The cadence below is tuned for
that phase — frequent minors, frequent patches, low ceremony. When the
project enters a stabilization phase, the rhythm slows down (longer
minor cycles, formal RCs) — see *Phase changes* at the bottom.

## Release types and cadence

Pythinker uses three release tracks. Patches and minors are both
common; majors are rare.

### 1. Patch releases — *any day, as soon as a fix is ready*

Patches ship bug fixes only and are **not gated on the minor schedule**.
A bug fix on `main` with green CI is enough to cut a patch — same day
as the merge if the maintainer wants. Patches between minors are the
norm, not the exception.

- **Source**: bug fix PRs that merge to `main`.
- **Minimum bar**: a regression test (or a justified explanation why
  one is impractical) and a `Fixed` bullet under `## [Unreleased]`.
- **Tag**: `vX.Y.(Z+1)`.
- **Cadence**: ad hoc — multiple patches per week is fine when bugs
  warrant it.
- **Batching rule**: if two bug fixes land within ~24h of each other,
  prefer one combined patch tag over two back-to-back tags. After 24h,
  cut the next one separately.

### 2. Minor releases — *every other Monday during active dev*

Minor releases roll up new behaviour. During active development they
ship every two weeks, alternating Mondays.

- **Cadence**: every other Monday, in the maintainer's timezone.
- **Cut from**: `main`. Features land on `dev` first, then are
  cherry-picked to `main` during the short freeze window.
- **Freeze window**: from the **Saturday before** the release Monday —
  only bug fixes and doc updates merge to `main` in this window.
- **RC build**: optional during active dev. Skip the RC for
  fix-heavy minors; publish `vX.Y.0-rc.1` the **Friday before** when a
  minor adds risky surface (new channel, new provider, new public API).
- **Skip rule**: if no user-visible change has landed since the last
  minor, the slot is **skipped silently** — no empty release.
- **Patches between minors**: explicitly allowed and expected. A patch
  cut on `vX.Y.Z` does not delay the next minor and does not consume
  the minor slot.

### 3. Major releases — *announced ≥30 days in advance*

Major releases ship breaking changes. They are rare and never surprise.

- **Announcement**: a tracking issue and a `MIGRATION.md` draft must
  exist on `main` at least 30 days before the planned tag.
- **Beta window**: a `-beta.N` line publishes during that 30-day window
  to give users time to migrate.
- **Date**: only on a Tuesday or Wednesday — gives the maintainer the
  rest of the week to triage post-release reports.

## Calendar artifacts

Two files serve as the source of truth for release dates:

| File | Owns | Updated when |
|------|------|--------------|
| `CHANGELOG.md` | What shipped, with dates | At every release (promote `[Unreleased]` → `[X.Y.Z] - YYYY-MM-DD`) |
| `docs/RELEASES.md` *(forward-looking)* | What will ship, with dates | When a release is planned, postponed, or skipped |

`docs/RELEASES.md` is the calendar — it lists upcoming RC and release
dates for the next two minor cycles. Anyone (contributor or user) can
read it to know when the next ship window is.

## Quality gates (every release, no exceptions)

Before any tag is pushed:

```bash
# 1. Lint — CI is strictest about F401 and F841
uv run ruff check pythinker --select F401,F841
uv run ruff check pythinker

# 2. Tests — full local matrix mirrors CI
uv run pytest tests/

# 3. Sanity build (catches packaging regressions)
uv build
twine check dist/*

# 4. Confirm CI on main is currently green
gh run list --branch main --limit 5 --json name,status,conclusion
```

After the tag is pushed and `publish.yml` finishes:

```bash
# 5. Verify PyPI propagation
curl -s https://pypi.org/pypi/pythinker-ai/json \
  | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"

# 6. Verify install-smoke (publish.yml runs this on ubuntu+macos+windows)
gh run view <publish-run-id>
```

If any of these fails, **the release is not done**. Yank or fix
forward — see *Rollback*.

## Branching contract

Pythinker uses a two-branch model:

- **`main`** — what gets released. Bug fixes, doc fixes, and
  cherry-picked stable features land here. PyPI publishes from tagged
  commits on `main` only.
- **`dev`** — experimental and breaking changes. Never merged whole
  into `main`; instead, individual stable features are cherry-picked.

Patch releases tag from `main` directly. Minor releases tag from
`main` after the freeze window closes. Major releases tag from `main`
at the end of the announced beta window.

There is **no `release/X.Y` long-lived branch**. Backports, when needed,
happen on `main` itself with a `vX.Y.Z` tag pointed at the backport
commit.

## CHANGELOG discipline

`CHANGELOG.md` follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).

- The top of the file always has a `## [Unreleased]` section, even when
  empty. Every PR that introduces user-visible behaviour change adds a
  bullet there.
- At release time, the `## [Unreleased]` header is renamed to
  `## [X.Y.Z] - YYYY-MM-DD`, and a fresh empty `## [Unreleased]` is
  inserted above it.
- Sections under each version use the standard headings: `Added`,
  `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.
- The CI publish step regex enforces the `## [X.Y.Z] - YYYY-MM-DD`
  header shape — get it wrong, the publish fails.

## Hotfixes

If a published release breaks production for users, a hotfix is a patch
release with the **smallest possible diff** to restore correctness.

1. Branch off `main` (`fix/<short-name>`), fix, add a regression test,
   open PR.
2. Run all quality gates locally and on CI.
3. After merge, tag immediately — no batching with unrelated work.
4. Publish a GitHub Release with `--generate-notes` and a one-paragraph
   "Why this hotfix" prelude.

## Rollback / yanking

PyPI does not allow re-uploading the same version. If a release is
broken:

- **Forward fix preferred.** Cut the next patch (`X.Y.Z+1`) within the
  same day if possible; mention the broken `X.Y.Z` in the new release
  notes.
- **Yank if forward-fix is delayed.** Use the PyPI web UI to mark the
  broken version yanked. `pip` will skip yanked versions in
  resolution but installs that pin the exact version still work
  (so existing deployments don't break).
- **Never delete a tag** that has been published. Reusing a tag is a
  supply-chain footgun.

## Automation gates (what is human, what is bot)

| Step | Who | Why |
|------|-----|-----|
| Decide whether to release | **Human** | Judgement call about scope and risk |
| Bump version + promote CHANGELOG | **Human (or `/schedule` agent)** | Two-file lockstep edit; needs review |
| Push tag, create GitHub Release | **Human** | Authoritative event |
| Build sdist + wheel, OIDC upload | **`publish.yml`** | Trusted Publishing — no tokens |
| Install-smoke on linux/mac/win | **`install-smoke.yml`** | Verifies the published artifact actually installs |
| Update PyPI badge | **shields.io** | Auto-fetches from PyPI JSON API |
| Announce release | **Human** | Optional; channel-dependent |

The `/schedule` skill can fully automate the routine bump+tag+release
cadence for minor releases — see *Recurring schedule* below.

## Recurring schedule

A maintainer can automate the bi-weekly minor cadence with the
`/schedule` skill:

```text
/schedule "every other Monday at 14:00 UTC"
   description: cut next minor release if [Unreleased] is non-empty
```

The scheduled agent's job:

1. Read `CHANGELOG.md`. If `[Unreleased]` has no bullets, post a
   summary "skipping this month — no user-visible changes" and exit.
2. Otherwise, propose: next minor version, the bumped files, the
   promoted CHANGELOG, and the tag. **Wait for human approval.**
3. After approval, push tag and create the GitHub Release.

The agent never auto-publishes; it gathers the materials and pauses
at the human-gated step. This is the durable pattern.

## Why date-driven and event-driven together

- **Event-driven** (the existing skill) makes the *mechanics* of a
  release reproducible. Anyone running the checklist gets the same
  result.
- **Date-driven** (this document) makes the *cadence* of releases
  predictable. Users can plan upgrades; contributors know when their
  PR will ship.

A repo with only event-driven releases ships erratically; a repo with
only date-driven releases ships even when there's nothing to ship.
The two layered together give us calm, regular, low-drama releases.

## Phase changes

The cadence above is tuned for **active development** — the current
phase. The project moves between three phases over its lifetime:

| Phase | Minor cadence | RC required | Freeze | Patch latitude |
|-------|---------------|-------------|--------|----------------|
| **Active dev** *(now)* | Every 2 weeks | Optional | Sat → Mon | Any day, ad hoc |
| **Stabilization** | Monthly (1st Mon) | Yes (Fri before) | Wed → Mon | Any day, batched |
| **Maintenance** | Quarterly | Yes (1 week before) | 2 weeks | Hotfix only |

A phase change is a deliberate decision, recorded by editing the
*Project phase* section at the top of this file and noting the change
in `CHANGELOG.md` under the relevant version.

## See also

- [`.agents/skills/pythinker-release/SKILL.md`](../.agents/skills/pythinker-release/SKILL.md) — the executable checklist
- [`CHANGELOG.md`](../CHANGELOG.md) — what shipped
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — branch and PR rules
- [`.github/workflows/publish.yml`](../.github/workflows/publish.yml) — publish pipeline
- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — test matrix
- [`.github/workflows/install-smoke.yml`](../.github/workflows/install-smoke.yml) — post-publish verification
