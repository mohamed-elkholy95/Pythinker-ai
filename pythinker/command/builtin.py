"""Built-in slash command handlers (compatibility shim).

The canonical implementations live under :mod:`pythinker.command.builtins`,
grouped by topic. This module re-exports every public name so existing
imports (and ``unittest.mock.patch`` targets) keep working.

In particular, ``cmd_restart`` and ``cmd_upgrade`` are kept as thin wrappers
that pass this module's :mod:`asyncio` and :mod:`os` references into the
implementation helpers in :mod:`pythinker.command.builtins.lifecycle`. That
preserves the patch contract for tests like
``tests/cli/test_restart_command.py`` which do::

    patch("pythinker.command.builtin.asyncio", new=fake_asyncio)
    patch("pythinker.command.builtin.os.execv")
"""

from __future__ import annotations

import asyncio
import os

from pythinker.bus.events import OutboundMessage
from pythinker.command.builtins.dream import (
    _extract_changed_files,  # noqa: F401  (kept for backwards compatibility)
    _format_changed_files,  # noqa: F401  (kept for backwards compatibility)
    _format_dream_log_content,  # noqa: F401  (kept for backwards compatibility)
    _format_dream_restore_list,  # noqa: F401  (kept for backwards compatibility)
    cmd_dream,
    cmd_dream_log,
    cmd_dream_restore,
)
from pythinker.command.builtins.format import (
    _escape_markdown_text,  # noqa: F401  (kept for backwards compatibility)
    _fenced_text,  # noqa: F401  (kept for backwards compatibility)
    _format_task_row,  # noqa: F401  (kept for backwards compatibility)
)
from pythinker.command.builtins.lifecycle import (
    _cmd_restart_impl,
    _cmd_upgrade_impl,
    build_help_text,  # noqa: F401  (re-exported for tests/channels)
    cmd_edit,
    cmd_help,
    cmd_new,
    cmd_regenerate,
    cmd_status,
    cmd_stop,
)
from pythinker.command.builtins.tasks import (
    _task_id_from_args,  # noqa: F401  (kept for backwards compatibility)
    _task_output_record_for_session,  # noqa: F401  (kept for backwards compatibility)
    _task_record_for_session,  # noqa: F401  (kept for backwards compatibility)
    cmd_task_output,
    cmd_task_stop,
    cmd_tasks,
)
from pythinker.command.router import CommandContext, CommandRouter


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv.

    Thin wrapper that injects this module's :mod:`asyncio` and :mod:`os`
    references into :func:`_cmd_restart_impl` so test patches at
    ``pythinker.command.builtin.asyncio`` and
    ``pythinker.command.builtin.os.execv`` remain effective.
    """
    return await _cmd_restart_impl(asyncio, os, ctx)


async def cmd_upgrade(ctx: CommandContext) -> OutboundMessage:
    """Check PyPI and upgrade pythinker in place; restart on success.

    Thin wrapper mirroring :func:`cmd_restart` so the same patch targets work
    here too.
    """
    return await _cmd_upgrade_impl(asyncio, os, ctx)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.priority("/regenerate", cmd_regenerate)
    router.priority("/edit", cmd_edit)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/tasks", cmd_tasks)
    router.exact("/task-output", cmd_task_output)
    router.prefix("/task-output ", cmd_task_output)
    router.exact("/task-stop", cmd_task_stop)
    router.prefix("/task-stop ", cmd_task_stop)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/help", cmd_help)
    router.exact("/upgrade", cmd_upgrade)
