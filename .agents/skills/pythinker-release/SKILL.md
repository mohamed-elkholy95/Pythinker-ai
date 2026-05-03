---
name: pythinker-release
description: Release a Pythinker version — bump, build, publish to PyPI/TestPyPI via OIDC Trusted Publishing, and manage GitHub releases.
metadata:
  pythinker:
    emoji: "🚀"
    requires:
      bins: ["git", "gh"]
---

# Pythinker Release Companion

Use when preparing or executing a release. PyPI and TestPyPI are wired
to GitHub Actions Trusted Publishing — **no tokens anywhere**.

## Prerequisites

- `.github/workflows/publish.yml` configured with `pypi` and
  `testpypi` environments (already set up)
- Working tree clean (`git status`)
- CI green on `main` (`gh run list --branch main --limit 5`)

## Version Bump — Two Files Must Sync

Every release touches **two files in lockstep**:

| File | Field |
|------|-------|
| `pyproject.toml` | `[project] version = "X.Y.Z"` |
| `pythinker/__init__.py` (~line 24) | the fallback literal in `_read_pyproject_version() or "X.Y.Z"` |

The `publish.yml` "Resolve package version" step rejects any release
event whose tag doesn't equal the `pyproject.toml` version — so the
fallback is the only safety net for source checkouts that don't include
`pyproject.toml`.

## Release Checklist

### 1. Pre-flight
```bash
git status
git log --oneline -5
gh run list --branch main --limit 5 --json name,status,conclusion
```

### 2. Bump version (both files)
```bash
# Edit pyproject.toml: [project] version = "2.1.0"
# Edit pythinker/__init__.py: ... or "2.1.0"
```

### 3. Commit + tag
```bash
git commit -am "release 2.1.0"
git tag v2.1.0
```

### 4. Push + create release
```bash
git push --follow-tags
gh release create v2.1.0 --generate-notes
```

The `release: published` event fires `publish.yml` → builds sdist +
wheel → uploads to PyPI via OIDC.

### 5. Verify
```bash
# After ~2 min
curl -s https://pypi.org/pypi/pythinker-ai/json | python -m json.tool | grep '"version"'
gh run list --ref main --limit 3
```

## TestPyPI Dry-Run

```bash
gh workflow run publish.yml -f target=testpypi --ref main
# Then: https://test.pypi.org/project/pythinker-ai/
```

## Hotfix on `main`

```bash
git checkout main
git pull --rebase origin main
# ... apply fix, commit "fix: ..." ...
git tag v1.2.3
git push --follow-tags
gh release create v1.2.3 --generate-notes
```

## What NOT To Do

- **Never** hardcode a version in the README PyPI badge
  (`README.md:9`). The badge URL `https://img.shields.io/pypi/v/pythinker-ai`
  is dynamic — shields.io fetches the live version from PyPI's JSON API.
- **Never** add `Co-Authored-By: Claude` trailers or "Generated with
  Claude Code" footers to commit messages or release notes.
- **Never** bypass hooks (`--no-verify`, `--no-gpg-sign`) without
  explicit user approval.
- **Never** merge `dev` into `main` as a whole — cherry-pick stable
  features.
- **Never** force-push to `main` or delete a published tag without
  explicit user approval.

## Boundaries

- Release pipeline edits, version bumps, and GitHub Releases need
  explicit user approval
- Dependency additions / pin changes need explicit approval and PR
  justification
- The `publish.yml` workflow is the authority — no manual `twine upload`
  out-of-band
