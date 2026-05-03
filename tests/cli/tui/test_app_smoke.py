"""Boot the TUI Application headlessly via prompt_toolkit's
create_pipe_input + DummyOutput, send a few keystrokes, and verify clean
exit."""
from __future__ import annotations

import pytest


@pytest.mark.timeout(10)
async def test_smoke_help_then_exit(tmp_path, monkeypatch) -> None:
    pytest.importorskip("prompt_toolkit.input")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from pythinker.cli.tui import TuiOptions
    from pythinker.cli.tui.app import run

    # create_pipe_input() returns a context manager in prompt_toolkit 3.0.50+
    with create_pipe_input() as inp:
        # Type "/exit", press Enter — dispatched by the Enter key binding
        inp.send_text("/exit\r")
        opts = TuiOptions(
            workspace=str(tmp_path),
            session_key="cli:tui",
            log_file=str(tmp_path / "tui.log"),
        )
        rc = await run(opts, _input=inp, _output=DummyOutput())
        assert rc == 0
