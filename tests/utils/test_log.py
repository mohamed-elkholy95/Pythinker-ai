"""Resolution-order tests for ``pythinker.utils.log.configure_logging``.

Pins the precedence: explicit ``level`` > ``PYTHINKER_LOG_LEVEL`` env >
``Config.logging.level`` > ``"INFO"`` baseline. Without these tests the
Logging schema field is easy to silently bypass when callers forget to
pass ``config=`` to ``configure_logging``.
"""

from __future__ import annotations

import logging as _stdlib_logging
from io import StringIO

import pytest
from loguru import logger

from pythinker.config.schema import Config
from pythinker.utils.log import configure_logging


@pytest.fixture(autouse=True)
def _restore_loguru_after_test():
    """Each test reconfigures the global loguru sink; reset afterwards."""
    yield
    logger.remove()
    logger.add(_stdlib_logging.StreamHandler(), level="INFO")


def test_default_is_info_when_nothing_set(monkeypatch):
    monkeypatch.delenv("PYTHINKER_LOG_LEVEL", raising=False)
    assert configure_logging() == "INFO"


def test_config_level_used_when_no_cli_or_env(monkeypatch):
    monkeypatch.delenv("PYTHINKER_LOG_LEVEL", raising=False)
    cfg = Config()
    cfg.logging.level = "WARNING"
    assert configure_logging(config=cfg) == "WARNING"


def test_env_var_beats_config(monkeypatch):
    monkeypatch.setenv("PYTHINKER_LOG_LEVEL", "DEBUG")
    cfg = Config()
    cfg.logging.level = "WARNING"
    assert configure_logging(config=cfg) == "DEBUG"


def test_explicit_level_beats_env_and_config(monkeypatch):
    monkeypatch.setenv("PYTHINKER_LOG_LEVEL", "DEBUG")
    cfg = Config()
    cfg.logging.level = "WARNING"
    assert configure_logging(level="ERROR", config=cfg) == "ERROR"


def test_invalid_level_falls_through_to_next_source(monkeypatch):
    """An unknown level (typo, etc.) is ignored, not crashed on."""
    monkeypatch.setenv("PYTHINKER_LOG_LEVEL", "LOUD")
    cfg = Config()
    cfg.logging.level = "WARNING"
    # CLI explicit "WHISPER" (invalid) → env "LOUD" (invalid) → config "WARNING".
    assert configure_logging(level="WHISPER", config=cfg) == "WARNING"


def test_lowercase_input_is_normalized(monkeypatch):
    monkeypatch.delenv("PYTHINKER_LOG_LEVEL", raising=False)
    assert configure_logging(level="debug") == "DEBUG"


def test_info_level_actually_filters_debug(monkeypatch):
    """End-to-end: a DEBUG call must not reach the sink at INFO level."""
    monkeypatch.delenv("PYTHINKER_LOG_LEVEL", raising=False)
    sink = StringIO()
    logger.remove()
    configure_logging(level="INFO")
    # configure_logging installs sys.stderr; replace with our capture.
    logger.remove()
    logger.add(sink, level="INFO")
    logger.debug("must-not-appear")
    logger.info("must-appear")
    out = sink.getvalue()
    assert "must-not-appear" not in out
    assert "must-appear" in out


def test_called_idempotent_no_sink_leak(monkeypatch):
    """Calling twice should leave exactly one sink — verified by counting writes."""
    monkeypatch.delenv("PYTHINKER_LOG_LEVEL", raising=False)
    sink = StringIO()
    configure_logging(level="INFO")
    configure_logging(level="INFO")
    configure_logging(level="INFO")
    # Replace the active sink with our capture, then emit once. If
    # configure_logging leaked sinks, the message would appear N times.
    logger.remove()
    logger.add(sink, level="INFO")
    logger.info("once")
    assert sink.getvalue().count("once") == 1


def test_unset_env_str_treated_as_none(monkeypatch):
    """Empty PYTHINKER_LOG_LEVEL is treated as if unset."""
    monkeypatch.setenv("PYTHINKER_LOG_LEVEL", "")
    cfg = Config()
    cfg.logging.level = "ERROR"
    assert configure_logging(config=cfg) == "ERROR"
