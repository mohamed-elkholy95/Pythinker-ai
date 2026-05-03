"""StatusScreen — read-only snapshot overlay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythinker.cli.tui.panes.overlay import OverlayScreen
from pythinker.cli.tui.status_snapshot import collect_status_snapshot

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


class StatusScreen(OverlayScreen):
    def __init__(self, app: "TuiApp") -> None:
        # Collect once at open time — this is a static snapshot overlay.
        self._snap = collect_status_snapshot(app)

    def render(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = [("class:status.brand", " status \n\n")]
        for k, v in self._snap.items():
            out.append(("", f"  {k:20s} : {v}\n"))
        out.append(("class:hint", "\n  press Esc to close\n"))
        return out
