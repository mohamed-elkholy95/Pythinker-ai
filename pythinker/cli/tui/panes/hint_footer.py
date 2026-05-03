"""1-row context-sensitive bottom hint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.layout.controls import FormattedTextControl

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiState
    from pythinker.cli.tui.panes.overlay import OverlayContainer


class HintFooter:
    def __init__(self, state: "TuiState", overlay: "OverlayContainer") -> None:
        self._state = state
        self._overlay = overlay
        self._control = FormattedTextControl(self.render)

    @property
    def control(self) -> FormattedTextControl:
        return self._control

    def render(self) -> list[tuple[str, str]]:
        if self._overlay.visible:
            return [("class:hint", "  ↑/↓ move  ·  Enter select  ·  Esc close  ·  type to filter")]
        if self._state.waiting:
            queued = len(getattr(self._state, "queued_messages", ()))
            queue_part = f"{queued} queued" if queued else "Enter queues next"
            return [
                (
                    "class:hint",
                    f"  ● thinking…  ·  {queue_part}  ·  double-Esc interrupt  ·  Ctrl+C stop turn",
                )
            ]
        return [("class:hint", "  Enter send  ·  Ctrl+J newline  ·  / commands  ·  Ctrl+C quit")]
