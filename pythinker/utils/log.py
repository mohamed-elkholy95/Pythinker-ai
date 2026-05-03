"""Loguru sink configuration."""

# Resolution order, highest priority first:
#   1. Explicit ``level`` argument (CLI flags: --verbose / --quiet)
#   2. ``PYTHINKER_LOG_LEVEL`` environment variable
#   3. ``Config.logging.level`` (from ~/.pythinker/config.json)
#   4. ``"INFO"`` as a baked-in safe default
# Idempotent: re-calling ``configure_logging`` replaces the existing sink, so
# a two-phase boot (early INFO before config load, then reconfigure after) is
# safe.

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pythinker.config.schema import Config

_VALID_LEVELS = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _normalize(level: str | None) -> str | None:
    if not level:
        return None
    upper = level.strip().upper()
    return upper if upper in _VALID_LEVELS else None


def configure_logging(
    *,
    level: str | None = None,
    config: "Config | None" = None,
) -> str:
    """(Re)install the loguru stderr sink at the resolved level.

    Returns the level that was actually applied so callers can log it
    back to the user (useful when --verbose was honoured implicitly via
    env or config).
    """
    resolved = (
        _normalize(level)
        or _normalize(os.environ.get("PYTHINKER_LOG_LEVEL"))
        or _normalize(getattr(getattr(config, "logging", None), "level", None))
        or "INFO"
    )
    logger.remove()
    logger.add(sys.stderr, level=resolved)
    return resolved


def configure_logging_early() -> str:
    """Install a safe-default sink before any config has been loaded.

    Called from CLI entry-points so the banner / preflight lines render
    at INFO instead of DEBUG. The full config-aware reconfigure happens
    later via ``configure_logging(config=cfg)``.
    """
    return configure_logging()
