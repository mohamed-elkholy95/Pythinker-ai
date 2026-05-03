"""Release-readiness checks.

Pure helpers + a ``run_checks()`` orchestrator. The CLI command
(``pythinker release check``) and the publish workflow share this module so
the same gate runs in CI and on a maintainer's laptop.

Status semantics
----------------

Each check returns a ``CheckResult`` with one of:

- ``ok``     — passed; nothing for the user to do.
- ``warn``   — non-blocking; e.g. ``[Unreleased]`` exists in CHANGELOG but
               we couldn't pin the section to the current version because
               the version is a 0.x dev tag.
- ``fail``   — blocking; the release MUST NOT proceed.
- ``skip``   — the check was intentionally not run (e.g. heavy checks
               without ``--build`` or no git tag at HEAD).

Callers decide whether ``warn`` is a hard failure for their context (CI
typically treats it as ok; ``--strict`` flips warn → fail).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from packaging.version import InvalidVersion, Version

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass(frozen=True)
class CheckResult:
    """One row in the release-readiness report."""

    name: str
    status: Status
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class CheckReport:
    """Aggregate result of a release-check run."""

    version: str | None
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """``True`` iff there are no ``fail`` rows."""
        return not any(r.status == "fail" for r in self.results)

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "warn"]

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "fail"]


# ---------------------------------------------------------------------------
# File-readers (used by individual checks; broken out for testability)
# ---------------------------------------------------------------------------

# Match the literal ``"X.Y.Z"`` (or any PEP 440 token) on the LHS of the assignment
# inside the ``[project]`` table. Hatchling validates this at build time, but we
# parse cheaply ourselves to avoid a tomllib read just for the version string.
_PYPROJECT_VERSION_RE = re.compile(
    r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE
)

# Match the hardcoded fallback in pythinker/__init__.py:
#   return _read_pyproject_version() or "X.Y.Z"
_INIT_FALLBACK_RE = re.compile(
    r'_read_pyproject_version\(\)\s*or\s*"([^"]+)"'
)


def read_pyproject_version(repo_root: Path) -> str | None:
    """Return the version string from ``pyproject.toml`` or ``None`` if absent."""
    path = repo_root / "pyproject.toml"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION_RE.search(text)
    return match.group(1) if match else None


def read_init_fallback(repo_root: Path) -> str | None:
    """Return the hardcoded fallback in ``pythinker/__init__.py`` or ``None``."""
    path = repo_root / "pythinker" / "__init__.py"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = _INIT_FALLBACK_RE.search(text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Individual checks (cheap)
# ---------------------------------------------------------------------------


def check_pep440_version(version: str | None) -> CheckResult:
    """Verify ``version`` parses as a PEP 440 release identifier."""
    if version is None:
        return CheckResult(
            name="pep440-version",
            status="fail",
            message="pyproject.toml does not declare a [project] version field",
        )
    try:
        Version(version)
    except InvalidVersion:
        return CheckResult(
            name="pep440-version",
            status="fail",
            message=f"version {version!r} is not a valid PEP 440 release identifier",
        )
    return CheckResult(
        name="pep440-version",
        status="ok",
        message=f"pyproject version is PEP 440-valid ({version})",
    )


def check_init_fallback_matches(repo_root: Path) -> CheckResult:
    """Verify ``pythinker/__init__.py`` fallback matches ``pyproject.toml``."""
    pyproject_version = read_pyproject_version(repo_root)
    init_fallback = read_init_fallback(repo_root)
    if pyproject_version is None or init_fallback is None:
        return CheckResult(
            name="init-fallback",
            status="fail",
            message="could not read version from pyproject.toml or pythinker/__init__.py",
            detail=f"pyproject={pyproject_version!r}, init_fallback={init_fallback!r}",
        )
    if init_fallback != pyproject_version:
        return CheckResult(
            name="init-fallback",
            status="fail",
            message=(
                f"pythinker/__init__.py fallback ({init_fallback!r}) does not match "
                f"pyproject.toml version ({pyproject_version!r})"
            ),
            detail=(
                "Source-only checkouts (no installed dist-info) read the fallback. "
                "If they drift, pythinker --version lies. Update both in lockstep."
            ),
        )
    return CheckResult(
        name="init-fallback",
        status="ok",
        message=f"pythinker/__init__.py fallback matches pyproject.toml ({pyproject_version})",
    )


def check_changelog_section(repo_root: Path, version: str | None) -> CheckResult:
    """Verify ``CHANGELOG.md`` has a ``## [VERSION] - YYYY-MM-DD`` section."""
    path = repo_root / "CHANGELOG.md"
    if not path.exists():
        return CheckResult(
            name="changelog-section",
            status="fail",
            message="CHANGELOG.md is missing",
            detail="The publish workflow gates on a per-version section.",
        )
    if version is None:
        return CheckResult(
            name="changelog-section",
            status="skip",
            message="cannot pin a CHANGELOG section without a known version",
        )
    text = path.read_text(encoding="utf-8")
    # Accept either:
    #   ## [2.0.0]
    #   ## [2.0.0] - 2026-04-30
    pattern = re.compile(
        rf"^##\s+\[{re.escape(version)}\](?:\s*-\s*\d{{4}}-\d{{2}}-\d{{2}})?\s*$",
        re.MULTILINE,
    )
    if not pattern.search(text):
        # Common authoring mistake — header still says [Unreleased]; tell the
        # user how to fix it rather than failing without context.
        if "## [Unreleased]" in text:
            return CheckResult(
                name="changelog-section",
                status="fail",
                message=(
                    f"CHANGELOG.md has [Unreleased] but no section for [{version}]. "
                    f"Promote [Unreleased] to [{version}] - YYYY-MM-DD before tagging."
                ),
            )
        return CheckResult(
            name="changelog-section",
            status="fail",
            message=f"CHANGELOG.md is missing a [{version}] section",
        )
    return CheckResult(
        name="changelog-section",
        status="ok",
        message=f"CHANGELOG.md has a section for [{version}]",
    )


def check_git_tag_matches(repo_root: Path, version: str | None) -> CheckResult:
    """Verify the current git HEAD's tag (if any) matches ``v{version}``.

    When HEAD has no tag (typical during pre-release work on main), the
    check is a ``skip`` rather than a failure.
    """
    if version is None:
        return CheckResult(
            name="git-tag-match",
            status="skip",
            message="no version to compare against",
        )
    git = shutil.which("git")
    if git is None:
        return CheckResult(
            name="git-tag-match",
            status="skip",
            message="git not on PATH",
        )
    try:
        result = subprocess.run(
            [git, "tag", "--points-at", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.SubprocessError as exc:
        return CheckResult(
            name="git-tag-match",
            status="skip",
            message=f"git failed: {exc}",
        )
    tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    if not tags:
        return CheckResult(
            name="git-tag-match",
            status="skip",
            message="HEAD has no tags (run before tagging or on a tagged commit)",
        )
    expected = f"v{version}"
    if expected in tags:
        return CheckResult(
            name="git-tag-match",
            status="ok",
            message=f"HEAD is tagged {expected}",
        )
    return CheckResult(
        name="git-tag-match",
        status="fail",
        message=(
            f"HEAD has tags {tags} but none matches the expected {expected}. "
            "Tag-vs-pyproject mismatch is enforced at publish time and will fail "
            "the release."
        ),
    )


# ---------------------------------------------------------------------------
# Heavy checks (opt-in via ``run_checks(build=True)``)
# ---------------------------------------------------------------------------


def check_build_succeeds(repo_root: Path) -> CheckResult:
    """Run ``python -m build`` from a temp dist dir and report success/failure.

    Heavy: spawns a subprocess, downloads build deps if missing, writes to
    ``dist/``. The maintainer is expected to clean ``dist/`` themselves.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "build"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except FileNotFoundError:
        return CheckResult(
            name="build",
            status="fail",
            message="python -m build is not installed",
            detail="pip install build",
        )
    except subprocess.SubprocessError as exc:
        return CheckResult(
            name="build",
            status="fail",
            message=f"python -m build failed: {exc}",
        )
    if result.returncode != 0:
        return CheckResult(
            name="build",
            status="fail",
            message="python -m build exited non-zero",
            detail=(result.stderr or result.stdout)[-500:],
        )
    return CheckResult(
        name="build",
        status="ok",
        message="python -m build succeeded (sdist + wheel in dist/)",
    )


def check_twine_check(repo_root: Path) -> CheckResult:
    """Run ``twine check dist/*`` to validate metadata before upload."""
    dist_dir = repo_root / "dist"
    if not dist_dir.exists() or not any(dist_dir.iterdir()):
        return CheckResult(
            name="twine-check",
            status="skip",
            message="dist/ is empty — run --build first",
        )
    artifacts = sorted(p for p in dist_dir.iterdir() if p.suffix in (".whl", ".gz"))
    if not artifacts:
        return CheckResult(
            name="twine-check",
            status="skip",
            message="no .whl or .tar.gz in dist/",
        )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "twine", "check", *(str(p) for p in artifacts)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError:
        return CheckResult(
            name="twine-check",
            status="fail",
            message="twine is not installed",
            detail="pip install twine",
        )
    except subprocess.SubprocessError as exc:
        return CheckResult(
            name="twine-check",
            status="fail",
            message=f"twine check failed: {exc}",
        )
    if result.returncode != 0:
        return CheckResult(
            name="twine-check",
            status="fail",
            message="twine check found metadata problems",
            detail=(result.stderr or result.stdout)[-500:],
        )
    return CheckResult(
        name="twine-check",
        status="ok",
        message=f"twine check passed for {len(artifacts)} artifact(s)",
    )


def check_wheel_filename(repo_root: Path, version: str | None) -> CheckResult:
    """Verify the built wheel's filename embeds the expected version."""
    if version is None:
        return CheckResult(
            name="wheel-filename",
            status="skip",
            message="no version to compare against",
        )
    dist_dir = repo_root / "dist"
    if not dist_dir.exists():
        return CheckResult(
            name="wheel-filename",
            status="skip",
            message="dist/ does not exist — run --build first",
        )
    wheels = sorted(dist_dir.glob("pythinker_ai-*.whl"))
    if not wheels:
        return CheckResult(
            name="wheel-filename",
            status="skip",
            message="no pythinker_ai wheel in dist/",
        )
    expected_token = f"-{version}-"
    matches = [w for w in wheels if expected_token in w.name]
    if not matches:
        names = [w.name for w in wheels]
        return CheckResult(
            name="wheel-filename",
            status="fail",
            message=f"no wheel filename matches version {version}",
            detail=f"found: {names}",
        )
    return CheckResult(
        name="wheel-filename",
        status="ok",
        message=f"wheel {matches[-1].name} embeds version {version}",
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_checks(repo_root: Path, *, build: bool = False) -> CheckReport:
    """Run the standard release-readiness battery.

    Cheap checks always run. Heavy checks (``build``, ``twine-check``,
    ``wheel-filename``) only run when ``build=True``.
    """
    version = read_pyproject_version(repo_root)
    results: list[CheckResult] = [
        check_pep440_version(version),
        check_init_fallback_matches(repo_root),
        check_changelog_section(repo_root, version),
        check_git_tag_matches(repo_root, version),
    ]
    if build:
        results.append(check_build_succeeds(repo_root))
        results.append(check_twine_check(repo_root))
        results.append(check_wheel_filename(repo_root, version))
    return CheckReport(version=version, results=results)


__all__ = [
    "CheckReport",
    "CheckResult",
    "Status",
    "check_build_succeeds",
    "check_changelog_section",
    "check_git_tag_matches",
    "check_init_fallback_matches",
    "check_pep440_version",
    "check_twine_check",
    "check_wheel_filename",
    "read_init_fallback",
    "read_pyproject_version",
    "run_checks",
]
