"""Single-line text-input overlay (paste-friendly, optionally masked).

Implements ``OverlayScreen`` so it lives inside the running prompt_toolkit
app. That matters for paste: clipboard contents arrive through the same
``<any>`` key binding the pickers use, so prompt_toolkit decodes
bracketed-paste sequences into individual character events. ``getpass`` /
``input()`` inside ``run_in_terminal`` cannot do that — they read raw bytes
from a tty in cooked mode and get tripped up by terminal paste filtering.
"""

from __future__ import annotations

import asyncio

from pythinker.cli.tui.panes.overlay import OverlayScreen


class InputDialogScreen(OverlayScreen):
    """Modal that captures one line of text and resolves a future.

    The caller awaits ``future`` (passed in or read off ``self.future``);
    Enter resolves with the captured text, Esc resolves with ``None``.
    Pre-existing key bindings in ``app.py`` already feed printable
    keystrokes (including pasted text) into ``set_query``, route Backspace
    to trim, and route Enter to ``commit`` — so this screen needs no new
    keybindings of its own.
    """

    def __init__(
        self,
        *,
        title: str,
        prompt: str,
        hint: str = "",
        mask: bool = False,
        future: asyncio.Future[str | None] | None = None,
    ) -> None:
        self._title = title
        self._prompt = prompt
        self._hint = hint
        self._mask = mask
        self._query = ""
        self.future: asyncio.Future[str | None] = (
            future if future is not None else asyncio.get_event_loop().create_future()
        )

    # OverlayScreen wiring -------------------------------------------------

    def set_query(self, q: str) -> None:
        self._query = q

    async def commit(self) -> None:
        if not self.future.done():
            self.future.set_result(self._query)

    def on_cancel(self) -> None:
        """Called by the app's Esc handler before the overlay is popped."""
        if not self.future.done():
            self.future.set_result(None)

    def render(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        out.append(("class:picker.title", f" {self._title.upper()} "))
        out.append(
            ("class:picker.meta", "  Enter save · Esc cancel\n")
        )
        if self._hint:
            for line in self._hint.splitlines():
                out.append(("class:picker.meta", f" {line}\n"))
        out.append(("class:picker.rule", "─" * 72 + "\n"))
        out.append(("class:picker.prompt", f" {self._prompt} "))
        if not self._query:
            out.append(("class:picker.query.placeholder", "(paste here, then Enter)\n"))
        elif self._mask:
            # Mask captured text but still surface the length so the user
            # can confirm the paste landed (and re-paste if it looks short).
            out.append(("class:picker.query", "•" * len(self._query)))
            out.append(
                ("class:picker.meta", f"   ({len(self._query)} chars)\n")
            )
        else:
            out.append(("class:picker.query", f"{self._query}\n"))
        return out
