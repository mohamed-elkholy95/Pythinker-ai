"""End-to-end navigation test for the TUI picker overlay.

Drives the prompt_toolkit Application via ``create_pipe_input`` + ``DummyOutput``
to verify that arrow-key navigation and Enter actually commit a picker choice.
This is the regression test for B-8 (picker overlays were rendered but
keyboard-dead) and I-10 (Enter task was unretained).

The ``/theme`` picker is used because ``THEMES`` is statically populated with
two entries (``default`` and ``monochrome``), so navigation always has a real
target. ``/sessions`` would be empty in a fresh tmp_path.
"""
from __future__ import annotations

import asyncio
import json

import pytest


async def test_picker_down_then_enter_selects_second_item(tmp_path) -> None:
    pytest.importorskip("prompt_toolkit.input")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from pythinker.cli.tui import TuiOptions
    from pythinker.cli.tui.app import run

    # Hermetic config — the /theme picker calls save_config() which would
    # otherwise overwrite the user's real ~/.pythinker/config.json.
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"cli": {"tui": {"theme": "default"}}}),
        encoding="utf-8",
    )

    async def _drive(inp) -> None:
        # Sequence:
        #   1) Open the theme picker via slash command.
        #   2) Down-arrow (CSI "\x1b[B") → cursor moves to "monochrome".
        #   3) Enter → commit (overlay's commit() runs as a task; picker pops).
        #   4) Wait briefly so the commit task finalizes BEFORE we send more
        #      keys — otherwise the overlay-visible filter swallows them as
        #      query input.
        #   5) /exit so run() returns rc=0.
        inp.send_text("/theme\r")
        await asyncio.sleep(0.1)
        inp.send_text("\x1b[B")
        await asyncio.sleep(0.05)
        inp.send_text("\r")
        await asyncio.sleep(0.2)
        inp.send_text("/exit\r")

    with create_pipe_input() as inp:
        opts = TuiOptions(
            workspace=str(tmp_path),
            session_key="cli:tui",
            config_path=str(config_path),
            log_file=str(tmp_path / "tui.log"),
        )
        # pytest-timeout is not registered; wrap with asyncio.wait_for so a
        # hung Application can't stall CI.
        driver = asyncio.create_task(_drive(inp))
        try:
            rc = await asyncio.wait_for(
                run(opts, _input=inp, _output=DummyOutput()),
                timeout=10,
            )
        finally:
            driver.cancel()
            try:
                await driver
            except (asyncio.CancelledError, Exception):
                pass

    assert rc == 0
    # The theme picker persists the selection to disk — proves the Enter
    # binding ran and didn't get GC'd before commit() finished.
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved.get("cli", {}).get("tui", {}).get("theme") == "monochrome"


async def test_picker_escape_closes_before_next_command(tmp_path) -> None:
    pytest.importorskip("prompt_toolkit.input")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from pythinker.cli.tui import TuiOptions
    from pythinker.cli.tui.app import run

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"cli": {"tui": {"theme": "default"}}}),
        encoding="utf-8",
    )

    async def _drive(inp) -> None:
        inp.send_text("/theme\r")
        await asyncio.sleep(0.1)
        inp.send_text("\x1b")
        await asyncio.sleep(0.05)
        inp.send_text("/exit\r")

    with create_pipe_input() as inp:
        opts = TuiOptions(
            workspace=str(tmp_path),
            session_key="cli:tui",
            config_path=str(config_path),
            log_file=str(tmp_path / "tui.log"),
        )
        driver = asyncio.create_task(_drive(inp))
        try:
            rc = await asyncio.wait_for(
                run(opts, _input=inp, _output=DummyOutput()),
                timeout=10,
            )
        finally:
            driver.cancel()
            try:
                await driver
            except (asyncio.CancelledError, Exception):
                pass

    assert rc == 0
