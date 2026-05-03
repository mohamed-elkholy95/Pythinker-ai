"""HelpScreen — static commands list overlay."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pythinker.cli.tui.panes.overlay import OverlayScreen


class HelpCommand(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def summary(self) -> str: ...


class HelpScreen(OverlayScreen):
    def __init__(self, commands: Sequence[HelpCommand]) -> None:
        self._commands = commands

    def render(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = [("class:status.brand", " help \n\n")]
        for c in self._commands:
            aliases = f"  ({', '.join('/' + a for a in c.aliases)})" if c.aliases else ""
            out.append(("", f"  /{c.name}{aliases}\n"))
            out.append(("class:hint", f"      {c.summary}\n"))
        out.append(("class:hint", "\n  press Esc to close\n"))
        return out
