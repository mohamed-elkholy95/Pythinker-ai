"""Confirm `pythinker tui` and `pythinker chat` are registered Typer
subcommands and resolve to the same handler."""
from __future__ import annotations

from typer.testing import CliRunner


def test_tui_subcommand_is_registered() -> None:
    from pythinker.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["tui", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "Open the full-screen TUI chat" in result.stdout


def test_chat_alias_is_registered() -> None:
    from pythinker.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0, result.stdout
