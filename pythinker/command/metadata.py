"""Metadata for every built-in slash command.

Single source of truth for ``build_help_text()`` (CLI ``/help``) and the
``GET /api/commands`` endpoint that powers the WebUI command palette.

Adding a new built-in command means three edits:
  1. Implement the handler in ``pythinker/command/builtin.py``.
  2. Register it via ``register_builtin_commands()`` in the same file.
  3. Append a ``CommandMeta`` row here.

Tests in ``tests/command/test_metadata.py`` enforce that step 3 is not
forgotten: any router-registered name without a metadata row fails CI.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandMeta:
    """One built-in command's display metadata.

    ``name`` is the canonical slash form, e.g. ``/dream-log``.
    ``summary`` is one short sentence shown in the palette and ``/help``.
    ``usage`` is an optional argument hint, e.g. ``/dream-log <sha>``; empty
    when the command takes no arguments.
    """

    name: str
    summary: str
    usage: str = ""


BUILTIN_COMMAND_METADATA: tuple[CommandMeta, ...] = (
    CommandMeta("/new", "Stop current task and start a new conversation"),
    CommandMeta("/stop", "Stop the current task"),
    CommandMeta("/restart", "Restart the bot"),
    CommandMeta("/status", "Show bot status"),
    CommandMeta(
        "/regenerate",
        "Drop the last assistant turn and re-run the prior user message",
    ),
    CommandMeta("/edit", "Rewrite a user message in place and re-run from there"),
    CommandMeta("/dream", "Manually trigger Dream consolidation"),
    CommandMeta(
        "/dream-log", "Show what the last Dream changed", usage="/dream-log [sha]"
    ),
    CommandMeta(
        "/dream-restore",
        "Revert memory to a previous state",
        usage="/dream-restore [sha]",
    ),
    CommandMeta("/help", "Show available commands"),
    CommandMeta(
        "/upgrade",
        "Check PyPI and upgrade pythinker in place (then restart)",
    ),
)
