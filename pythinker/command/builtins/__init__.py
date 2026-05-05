"""Built-in slash command handlers, grouped by topic.

The canonical handlers live in:

* :mod:`pythinker.command.builtins.lifecycle` — session and process lifecycle
  (``/new``, ``/stop``, ``/status``, ``/help``, ``/regenerate``, ``/edit``,
  plus restart/upgrade implementation helpers)
* :mod:`pythinker.command.builtins.dream` — Dream consolidation commands
  (``/dream``, ``/dream-log``, ``/dream-restore``)
* :mod:`pythinker.command.builtins.tasks` — autonomous task commands
  (``/tasks``, ``/task-output``, ``/task-stop``)
* :mod:`pythinker.command.builtins.format` — shared formatting helpers

The shim at :mod:`pythinker.command.builtin` re-exports every public name so
existing imports (and ``unittest.mock.patch`` targets) keep working.
"""

from __future__ import annotations
