"""Tests for Pydantic config schema."""

from __future__ import annotations

from pythinker.config.schema import Config


def test_cli_tui_theme_default() -> None:
    cfg = Config()
    assert cfg.cli.tui.theme == "default"


def test_cli_tui_theme_round_trip_camel_case() -> None:
    cfg = Config()
    cfg.cli.tui.theme = "monochrome"
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["cli"]["tui"]["theme"] == "monochrome"
    restored = Config.model_validate(dumped)
    assert restored.cli.tui.theme == "monochrome"
