"""
pythinker - A lightweight AI agent framework
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("pythinker-ai")
    except PackageNotFoundError:
        # Source checkouts often import pythinker without installed dist-info.
        return _read_pyproject_version() or "2.1.0"


__version__ = _resolve_version()
__logo__ = "🐍"

from pythinker.pythinker import (  # noqa: E402  -- imported after metadata so submodule can read __version__
    Pythinker,
    RunResult,
)

__all__ = ["Pythinker", "RunResult"]
