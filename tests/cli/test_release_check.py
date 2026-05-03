"""Tests for the release-readiness check helpers + the
``pythinker release check`` Typer command.

Coverage focuses on the pure helpers (filesystem in tmp_path, no network) and
on the small bit of CLI plumbing that translates report severity to exit
codes. Heavy checks (`python -m build`, twine check) are exercised via
monkey-patched subprocess calls so the tests stay fast and offline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pythinker.cli.commands import app
from pythinker.release.checks import (
    CheckResult,
    check_changelog_section,
    check_git_tag_matches,
    check_init_fallback_matches,
    check_pep440_version,
    check_wheel_filename,
    read_init_fallback,
    read_pyproject_version,
    run_checks,
)


def _write_pyproject(root: Path, version: str) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "pythinker-ai"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def _write_init(root: Path, fallback: str) -> None:
    pkg = root / "pythinker"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        f'def _resolve_version():\n    return _read_pyproject_version() or "{fallback}"\n',
        encoding="utf-8",
    )


def _write_changelog(root: Path, section: str) -> None:
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {section}\n\n- something\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_read_pyproject_version_roundtrip(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "2.0.0")
    assert read_pyproject_version(tmp_path) == "2.0.0"


def test_read_pyproject_missing(tmp_path: Path) -> None:
    assert read_pyproject_version(tmp_path) is None


def test_read_init_fallback_roundtrip(tmp_path: Path) -> None:
    _write_init(tmp_path, "2.0.0")
    assert read_init_fallback(tmp_path) == "2.0.0"


def test_read_init_fallback_missing(tmp_path: Path) -> None:
    assert read_init_fallback(tmp_path) is None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def test_check_pep440_accepts_valid() -> None:
    assert check_pep440_version("2.0.0").status == "ok"
    assert check_pep440_version("2.0.0a1").status == "ok"
    assert check_pep440_version("2.0.0.post1").status == "ok"


def test_check_pep440_rejects_invalid() -> None:
    result = check_pep440_version("not-a-version")
    assert result.status == "fail"
    assert "PEP 440" in result.message


def test_check_pep440_rejects_none() -> None:
    result = check_pep440_version(None)
    assert result.status == "fail"
    assert "[project] version" in result.message


def test_check_init_fallback_match(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "2.0.0")
    _write_init(tmp_path, "2.0.0")
    result = check_init_fallback_matches(tmp_path)
    assert result.status == "ok"


def test_check_init_fallback_drift(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "2.0.0")
    _write_init(tmp_path, "1.9.9")
    result = check_init_fallback_matches(tmp_path)
    assert result.status == "fail"
    assert "1.9.9" in result.message
    assert "2.0.0" in result.message


def test_check_init_fallback_missing_pyproject(tmp_path: Path) -> None:
    _write_init(tmp_path, "2.0.0")
    assert check_init_fallback_matches(tmp_path).status == "fail"


def test_check_changelog_section_with_date(tmp_path: Path) -> None:
    _write_changelog(tmp_path, "[2.0.0] - 2026-04-30")
    result = check_changelog_section(tmp_path, "2.0.0")
    assert result.status == "ok"


def test_check_changelog_section_without_date(tmp_path: Path) -> None:
    _write_changelog(tmp_path, "[2.0.0]")
    result = check_changelog_section(tmp_path, "2.0.0")
    assert result.status == "ok"


def test_check_changelog_only_unreleased(tmp_path: Path) -> None:
    _write_changelog(tmp_path, "[Unreleased]")
    result = check_changelog_section(tmp_path, "2.0.0")
    assert result.status == "fail"
    assert "[Unreleased]" in result.message
    assert "Promote" in result.message


def test_check_changelog_missing_file(tmp_path: Path) -> None:
    result = check_changelog_section(tmp_path, "2.0.0")
    assert result.status == "fail"
    assert "missing" in result.message


def test_check_changelog_skip_without_version(tmp_path: Path) -> None:
    _write_changelog(tmp_path, "[Unreleased]")
    assert check_changelog_section(tmp_path, None).status == "skip"


def test_check_git_tag_skips_when_head_untagged(tmp_path: Path) -> None:
    """A non-tagged commit is normal during pre-release work — must be skip, not fail."""
    # init a real tiny repo so `git tag --points-at HEAD` runs but returns ""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    result = check_git_tag_matches(tmp_path, "2.0.0")
    assert result.status == "skip"


def test_check_git_tag_matches_when_present(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "tag", "v2.0.0"], cwd=tmp_path, check=True)
    result = check_git_tag_matches(tmp_path, "2.0.0")
    assert result.status == "ok"


def test_check_git_tag_mismatch(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "tag", "v1.9.9"], cwd=tmp_path, check=True)
    result = check_git_tag_matches(tmp_path, "2.0.0")
    assert result.status == "fail"
    assert "v1.9.9" in result.message
    assert "v2.0.0" in result.message


def test_check_wheel_filename_match(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pythinker_ai-2.0.0-py3-none-any.whl").write_bytes(b"")
    result = check_wheel_filename(tmp_path, "2.0.0")
    assert result.status == "ok"


def test_check_wheel_filename_mismatch(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pythinker_ai-1.9.9-py3-none-any.whl").write_bytes(b"")
    result = check_wheel_filename(tmp_path, "2.0.0")
    assert result.status == "fail"


def test_check_wheel_filename_missing_dist(tmp_path: Path) -> None:
    """When dist/ is absent, this should skip rather than fail; --build is opt-in."""
    result = check_wheel_filename(tmp_path, "2.0.0")
    assert result.status == "skip"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def test_run_checks_clean_repo_passes(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "2.0.0")
    _write_init(tmp_path, "2.0.0")
    _write_changelog(tmp_path, "[2.0.0] - 2026-04-30")
    report = run_checks(tmp_path, build=False)
    assert report.passed, [r for r in report.results if r.status == "fail"]
    assert report.version == "2.0.0"


def test_run_checks_drift_fails(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "2.0.0")
    _write_init(tmp_path, "1.9.9")
    _write_changelog(tmp_path, "[2.0.0] - 2026-04-30")
    report = run_checks(tmp_path, build=False)
    assert not report.passed
    assert any(r.name == "init-fallback" and r.status == "fail" for r in report.results)


# ---------------------------------------------------------------------------
# CLI command (`pythinker release check`)
# ---------------------------------------------------------------------------


@pytest.fixture
def _wide_terminal(monkeypatch):
    """Force wide rendering so Rich doesn't truncate paths inside CLI output."""
    monkeypatch.setenv("COLUMNS", "200")


def test_cli_release_check_passes(_wide_terminal, monkeypatch) -> None:
    """``pythinker release check`` exits 0 when all checks pass."""

    def _fake_run_checks(_root, *, build=False):
        from pythinker.release.checks import CheckReport

        return CheckReport(
            version="2.0.0",
            results=[
                CheckResult(name="pep440-version", status="ok", message="ok"),
                CheckResult(name="init-fallback", status="ok", message="ok"),
                CheckResult(name="changelog-section", status="ok", message="ok"),
                CheckResult(name="git-tag-match", status="skip", message="no tag"),
            ],
        )

    monkeypatch.setattr("pythinker.release.checks.run_checks", _fake_run_checks)

    runner = CliRunner()
    result = runner.invoke(app, ["release", "check"])
    assert result.exit_code == 0, result.stdout


def test_cli_release_check_fails_on_failure(_wide_terminal, monkeypatch) -> None:
    """A single fail row should propagate as exit code 1."""

    def _fake_run_checks(_root, *, build=False):
        from pythinker.release.checks import CheckReport

        return CheckReport(
            version="2.0.0",
            results=[
                CheckResult(name="init-fallback", status="fail", message="drift"),
            ],
        )

    monkeypatch.setattr("pythinker.release.checks.run_checks", _fake_run_checks)
    runner = CliRunner()
    result = runner.invoke(app, ["release", "check"])
    assert result.exit_code == 1


def test_cli_release_check_strict_promotes_warn(_wide_terminal, monkeypatch) -> None:
    """``--strict`` flips warn → fail."""

    def _fake_run_checks(_root, *, build=False):
        from pythinker.release.checks import CheckReport

        return CheckReport(
            version="2.0.0",
            results=[
                CheckResult(name="changelog-section", status="warn", message="meh"),
            ],
        )

    monkeypatch.setattr("pythinker.release.checks.run_checks", _fake_run_checks)
    runner = CliRunner()

    # Default — warn alone shouldn't fail.
    result = runner.invoke(app, ["release", "check"])
    assert result.exit_code == 0

    # Strict — warn becomes fail.
    result_strict = runner.invoke(app, ["release", "check", "--strict"])
    assert result_strict.exit_code == 1
