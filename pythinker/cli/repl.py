"""Interactive REPL plumbing: prompt_toolkit session, history, terminal restore.

Carved out of ``pythinker/cli/commands.py`` per the §E1 simplification plan.
The mutable session/terminal state (``_PROMPT_SESSION``, ``_SAVED_TERM_ATTRS``)
lives in ``commands.py`` because tests directly assign to and read from those
attributes (``tests/cli/test_cli_input.py``); the helpers here read/write
those attributes through ``pythinker.cli.commands`` so a monkeypatch like
``patch("pythinker.cli.commands._PROMPT_SESSION", mock)`` works on this code
path too.
"""

from __future__ import annotations

import os
import select
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout


class SafeFileHistory(FileHistory):
    """FileHistory subclass that sanitizes surrogate characters on write.

    On Windows, special Unicode input (emoji, mixed-script) can produce
    surrogate characters that crash prompt_toolkit's file write.
    See issue #2846.
    """

    def store_string(self, string: str) -> None:
        safe = string.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        super().store_string(safe)


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    from pythinker.cli import commands

    if commands._SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, commands._SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    from pythinker.cli import commands

    # Save terminal state so we can restore it on exit
    try:
        import termios

        commands._SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from pythinker.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    # Honour test-time monkeypatches against ``pythinker.cli.commands``
    # for ``PromptSession`` (see tests/cli/test_cli_input.py).
    prompt_session_cls = getattr(commands, "PromptSession", PromptSession)
    safe_file_history_cls = getattr(commands, "SafeFileHistory", SafeFileHistory)

    commands._PROMPT_SESSION = prompt_session_cls(
        history=safe_file_history_cls(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    from pythinker.cli import commands

    if commands._PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    # Honour ``patch("pythinker.cli.commands.patch_stdout")`` from tests by
    # routing through the commands namespace.
    patch_stdout_fn = getattr(commands, "patch_stdout", patch_stdout)
    try:
        with patch_stdout_fn():
            return await commands._PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc
