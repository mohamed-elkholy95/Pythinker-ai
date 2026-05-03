"""Guard the maintainer skill tree under .agents/skills/.

Maintainer skills are not loaded by the runtime (`pythinker/agent/skills.py`
only resolves `<workspace>/skills` and the bundled tree), but they share the
same SKILL.md spec. This test asserts:

- every `.agents/skills/<name>/` passes the canonical validator
- in-band file references resolve to real files in the repo

Drift here usually means a refactor moved a module or constant without
updating the playbook the next coding agent will read.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
CANONICAL_VALIDATOR = (
    REPO_ROOT
    / "pythinker"
    / "skills"
    / "skill-creator"
    / "scripts"
    / "quick_validate.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "_pythinker_quick_validate", CANONICAL_VALIDATOR
    )
    assert spec is not None and spec.loader is not None, (
        f"Cannot load canonical validator at {CANONICAL_VALIDATOR}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_skill


def _skill_dirs() -> list[Path]:
    if not AGENTS_SKILLS_DIR.exists():
        return []
    return sorted(p for p in AGENTS_SKILLS_DIR.iterdir() if p.is_dir())


def test_agents_skills_dir_exists() -> None:
    assert AGENTS_SKILLS_DIR.is_dir(), (
        f"{AGENTS_SKILLS_DIR} is the maintainer skill home; do not remove it"
    )


@pytest.mark.parametrize(
    "skill_dir",
    _skill_dirs(),
    ids=lambda p: p.name,
)
def test_skill_passes_canonical_validator(skill_dir: Path) -> None:
    validate_skill = _load_validator()
    valid, message = validate_skill(skill_dir)
    assert valid, f"{skill_dir.name}: {message}"


# Match references like `pythinker/agent/loop.py` or
# `pythinker/agent/loop.py:189`. Skip code-fenced blocks to avoid grabbing
# illustrative snippets like `pythinker/channels/<name>.py`.
_PATH_RE = re.compile(r"`(pythinker/[A-Za-z0-9_./-]+\.py)(?::\d+(?:-\d+)?)?`")
_CODE_FENCE = "```"


def _references_in(skill_md: Path) -> set[str]:
    refs: set[str] = set()
    in_fence = False
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(_CODE_FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _PATH_RE.finditer(line):
            refs.add(match.group(1))
    return refs


@pytest.mark.parametrize(
    "skill_dir",
    _skill_dirs(),
    ids=lambda p: p.name,
)
def test_skill_file_references_resolve(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    missing = sorted(
        ref for ref in _references_in(skill_md) if not (REPO_ROOT / ref).is_file()
    )
    assert not missing, (
        f"{skill_dir.name} references files that no longer exist: {missing}. "
        "Update the SKILL.md or the moved module."
    )
