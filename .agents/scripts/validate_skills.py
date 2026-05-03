#!/usr/bin/env python3
"""Validate every maintainer skill under .agents/skills/.

Wraps the canonical validator at
``pythinker/skills/skill-creator/scripts/quick_validate.py`` so the
``.agents/`` tree obeys the same SKILL.md spec the runtime enforces.

Usage::

    uv run python .agents/scripts/validate_skills.py
    uv run python .agents/scripts/validate_skills.py path/to/SKILL/dir ...
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    if not CANONICAL_VALIDATOR.exists():
        raise SystemExit(
            f"Canonical validator not found at {CANONICAL_VALIDATOR}. "
            "Has pythinker/skills/skill-creator/ moved?"
        )
    spec = importlib.util.spec_from_file_location(
        "_pythinker_quick_validate", CANONICAL_VALIDATOR
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load validator from {CANONICAL_VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_skill


def _iter_targets(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(arg).resolve() for arg in argv]
    if not AGENTS_SKILLS_DIR.exists():
        return []
    return sorted(p for p in AGENTS_SKILLS_DIR.iterdir() if p.is_dir())


def main(argv: list[str]) -> int:
    validate_skill = _load_validator()
    targets = _iter_targets(argv)
    if not targets:
        print(f"No skills found under {AGENTS_SKILLS_DIR}")
        return 0

    failures: list[tuple[Path, str]] = []
    for target in targets:
        valid, message = validate_skill(target)
        marker = "ok" if valid else "FAIL"
        rel = target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target
        print(f"[{marker}] {rel}: {message}")
        if not valid:
            failures.append((target, message))

    if failures:
        print(f"\n{len(failures)} skill(s) failed validation.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
