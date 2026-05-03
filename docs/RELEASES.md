# Releases — Forward Calendar

This is the source of truth for **planned** Pythinker releases. The
schedule covers the next ~2 months. Cadence rules and rationale live
in [`release-cadence.md`](./release-cadence.md).

Past releases are recorded in [`../CHANGELOG.md`](../CHANGELOG.md).

> Pythinker is currently in **active development** — minors ship every
> two weeks and patches ship any day a bug fix is ready. Dates use the
> maintainer's local calendar day. If a date falls on a public holiday
> or otherwise unworkable day, it shifts to the next business day and
> this file is updated.

## Patch releases

Patch releases ship **as soon as a bug fix is ready** — they do not
wait for the minor schedule and they do not delay the next minor.
Track open patch candidates in the GitHub issues labelled
`patch-candidate`.

Active-dev rule of thumb:
- Two fixes within ~24h → one combined patch tag.
- Beyond 24h → cut the next patch separately.
- Same-day patch is fine (we shipped 2.0.1 and 2.0.2 on 2026-05-01).

## Minor releases

Minor releases ship on **every other Monday** during active dev. RCs
are optional — published the **Friday before** only when the minor
adds risky surface (new channel, new provider, new public API). The
slot is **skipped silently** if `[Unreleased]` in `CHANGELOG.md` has
no entries the preceding Saturday.

| Version | RC date (Fri, optional) | Release date (Mon) | Status |
|---------|------------------------|--------------------|--------|
| 2.1.0   | 2026-05-15 *(if needed)* | 2026-05-18 | Planned |
| 2.2.0   | 2026-05-29 *(if needed)* | 2026-06-01 | Planned |
| 2.3.0   | 2026-06-12 *(if needed)* | 2026-06-15 | Planned |
| 2.4.0   | 2026-06-26 *(if needed)* | 2026-06-29 | Planned |

Freeze window for each: from the **Saturday before** the release
Monday. Only bug fixes and doc updates merge to `main` during freeze.

## Major releases

None planned. A major release requires a 30-day announcement window
(see `release-cadence.md`).

## Recently shipped

| Version | Date       | Type   | Notes |
|---------|------------|--------|-------|
| 2.0.2   | 2026-05-01 | patch  | Onboarding wizard regressions ([PR #7](https://github.com/mohamed-elkholy95/Pythinker-ai/pull/7)) |
| 2.0.1   | 2026-05-01 | patch  | Admin dashboard, usage ledger, config editing API |

## How this file is updated

- When a release ships: move it from the planned table to *Recently
  shipped*.
- When a patch ships between minors: add it to *Recently shipped* but
  do not adjust the minor schedule.
- When a release is postponed: update the **Status** column with a
  short reason and a new target date.
- When a slot is skipped: set **Status** to `Skipped — no [Unreleased]
  entries on YYYY-MM-DD` and the next planned slot already exists in
  the table below.
- When the project moves out of active dev (to stabilization or
  maintenance), update the cadence note above and revise the table
  spacing — see *Phase changes* in `release-cadence.md`.
- A scheduled `/schedule` agent (see `release-cadence.md` *Recurring
  schedule*) keeps this file fresh by proposing edits each cycle.
