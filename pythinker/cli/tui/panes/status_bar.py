"""1-row top status bar: brand · model · session · tokens · workspace."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from prompt_toolkit.layout.controls import FormattedTextControl

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiState

_ACTIVE_DOT_FRAMES = ("●", "◉", "●", "◍")


def _shorten(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    if max_len <= 1:
        return "…"
    return "…" + value[-(max_len - 1):]


class StatusBar:
    def __init__(self, state: "TuiState") -> None:
        self._state = state
        self._control = FormattedTextControl(self.render)

    @property
    def control(self) -> FormattedTextControl:
        return self._control

    def refresh(self) -> None:
        # FormattedTextControl re-evaluates render() on every redraw, so the
        # only thing we have to do is ask the application to invalidate.
        # The app calls invalidate() after state mutations.
        return

    def _status_dot(self) -> tuple[str, str]:
        if not self._state.waiting:
            return "class:status.dot.idle", "○"
        frame = int(time.monotonic() * 6) % len(_ACTIVE_DOT_FRAMES)
        return "class:status.dot.active", _ACTIVE_DOT_FRAMES[frame]

    def _segment(self, label: str, value: str) -> list[tuple[str, str]]:
        return [
            ("class:status.sep", "  •  "),
            ("class:status.label", f"{label} "),
            ("class:status.value", value),
        ]

    def render(self) -> list[tuple[str, str]]:
        s = self._state
        ws_short = str(s.workspace).replace(str(s.workspace.home()), "~", 1) \
            if hasattr(s.workspace, "home") else str(s.workspace)
        ws_short = _shorten(ws_short, 28)
        model_short = _shorten(s.model, 28)
        provider_short = _shorten(s.provider, 18)
        session_short = _shorten(s.session_key, 22)
        dot_style, dot = self._status_dot()
        fragments = [
            ("class:status", " "),
            (dot_style, dot),
            ("class:status.brand", " Pythinker"),
        ]
        fragments.extend(self._segment("provider", provider_short))
        fragments.extend(self._segment("model", model_short))
        fragments.extend(self._segment("session", session_short))
        fragments.extend(self._segment("tokens", f"{s.last_turn_tokens:,} last"))
        fragments.extend(self._segment("ws", ws_short))
        queued = len(getattr(s, "queued_messages", ()))
        if queued:
            fragments.extend(self._segment("queue", str(queued)))
        fragments.append(("class:status", " "))
        return fragments
