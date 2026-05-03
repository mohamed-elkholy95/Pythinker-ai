"""tui_logging_redirect installs a single file sink for its lifetime and
restores prior loguru sinks on exit, even when an exception is raised."""
from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger


def _handler_count() -> int:
    return len(logger._core.handlers)  # noqa: SLF001 — loguru exposes no public API for this


def test_redirect_routes_logs_to_file(tmp_path: Path) -> None:
    from pythinker.cli.tui.logging_sink import tui_logging_redirect

    log_file = tmp_path / "tui.log"
    with tui_logging_redirect(log_file, level="DEBUG"):
        logger.info("hello-from-tui")
    assert log_file.exists()
    assert "hello-from-tui" in log_file.read_text()


def test_redirect_restores_prior_sinks_on_exit(tmp_path: Path) -> None:
    from pythinker.cli.tui.logging_sink import tui_logging_redirect

    before = _handler_count()
    with tui_logging_redirect(tmp_path / "tui.log"):
        pass
    assert _handler_count() == before


def test_redirect_restores_prior_sinks_on_exception(tmp_path: Path) -> None:
    from pythinker.cli.tui.logging_sink import tui_logging_redirect

    before = _handler_count()
    with pytest.raises(RuntimeError, match="boom"):
        with tui_logging_redirect(tmp_path / "tui.log"):
            raise RuntimeError("boom")
    assert _handler_count() == before
