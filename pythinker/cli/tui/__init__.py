"""pythinker tui — full-screen prompt_toolkit chat against AgentLoop."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TuiOptions", "run_tui"]


@dataclass(frozen=True)
class TuiOptions:
    workspace: str | None = None
    session_key: str = "cli:tui"
    config_path: str | None = None
    theme: str | None = None
    log_file: str | None = None


async def run_tui(opts: TuiOptions) -> int:
    """Launch the TUI. Returns the desired process exit code."""
    from pythinker.cli.tui.app import run as _run
    return await _run(opts)
