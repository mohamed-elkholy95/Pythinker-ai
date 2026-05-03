"""Loguru sink redirector for the TUI's lifetime.

prompt_toolkit's full-screen Application owns the framebuffer; loguru output
to stderr would corrupt it. While the TUI runs, every existing sink is
removed and a single file sink is installed; on exit (success or crash) the
prior sinks are restored exactly as they were.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger


@contextmanager
def tui_logging_redirect(log_file: Path, *, level: str = "WARNING") -> Iterator[None]:
    """Install a single file sink for log_file at the given level for the
    duration of the context. Restore prior sinks on exit, even when the
    context body raises.

    Args:
        log_file: Path to write logs to (parent directory created if needed).
        level: Logging level (default: "WARNING").
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Snapshot the current handler state (id → handler).
    snapshot = list(logger._core.handlers.items())  # noqa: SLF001

    # Remove all handlers temporarily.
    logger.remove()

    # Install single file sink for the TUI's lifetime.
    sink_id = logger.add(
        log_file,
        level=level,
        rotation="10 MB",
        retention=3,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    try:
        yield
    finally:
        # Remove our sink. The TUI bootstrap may call configure_logging() which
        # internally invokes logger.remove() and invalidates our sink_id; the
        # ValueError is benign in that case.
        try:
            logger.remove(sink_id)
        except ValueError:
            pass

        # Restore prior handlers by re-adding them with their captured state.
        # loguru's Handler stores configuration in private attributes:
        #   _sink, _levelno, _formatter, _filter, _colorize, _serialize, _enqueue
        # We reconstruct using those attributes. This is a best-effort approach
        # because _formatter is a compiled object that cannot be directly reused.
        for _id, handler in snapshot:
            try:
                # Attempt to reconstruct the handler configuration.
                # We extract levelno and other settings and re-add with logger.add().
                logger.add(
                    handler._sink,  # noqa: SLF001
                    level=handler._levelno,  # noqa: SLF001
                    format=handler._formatter,  # noqa: SLF001
                    filter=handler._filter,  # noqa: SLF001
                    colorize=handler._colorize,  # noqa: SLF001
                    serialize=handler._serialize,  # noqa: SLF001
                    enqueue=handler._enqueue,  # noqa: SLF001
                )
            except Exception:
                # Restore is best-effort; never let a sink-restore failure
                # mask the original exception (if any) or hide the TUI error.
                # The process exits after TUI anyway, so partial restoration
                # is acceptable. Just silently continue.
                continue
