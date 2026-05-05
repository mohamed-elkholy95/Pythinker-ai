"""Completion popup that visually highlights the top match.

prompt_toolkit's stock ``CompletionsMenuControl`` only paints a row with
``class:completion-menu.completion.current`` styling when the buffer's
``CompletionState.complete_index`` is set to that row's index — and the only
way to set ``complete_index`` is via ``Buffer.go_to_completion``, which
*also* inserts the completion's text into the buffer as a preview. That
preview-insert breaks plain typing and Backspace because every text change
re-fires the completion hook and re-inserts the preview, undoing edits.

This subclass keeps ``complete_index`` at ``None`` (so the buffer is never
mutated), but lies about it during rendering: temporarily sets it to ``0``
while ``super().create_content`` reads it, then restores ``None``. The menu
paints row 0 as if it were selected; the buffer stays untouched. The Enter
keybinding in ``app.py`` separately accepts row 0 when ``complete_index`` is
``None`` so a single Enter both accepts and submits the top match.
"""

from __future__ import annotations

from typing import Callable

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import FilterOrBool, has_completions, is_done, to_filter
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    ScrollOffsets,
    Window,
)
from prompt_toolkit.layout.controls import UIContent
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl


class _HighlightFirstCompletionsMenuControl(CompletionsMenuControl):
    """Render row 0 as ``current`` when no row is actually selected."""

    def create_content(self, width: int, height: int) -> UIContent:
        buf = get_app().current_buffer
        state = buf.complete_state
        if (
            state is not None
            and state.complete_index is None
            and state.completions
        ):
            # Lie about the index for the duration of one render. The
            # parent reads ``state.complete_index`` once into a local and
            # captures it in a closure, so flipping it back to ``None``
            # immediately after super() returns is safe — no other
            # subsystem sees the temporary 0.
            state.complete_index = 0
            try:
                return super().create_content(width, height)
            finally:
                state.complete_index = None
        return super().create_content(width, height)


class HighlightFirstCompletionsMenu(ConditionalContainer):
    """Drop-in replacement for prompt_toolkit's ``CompletionsMenu``.

    Same constructor surface; differs only in that the underlying control is
    ``_HighlightFirstCompletionsMenuControl`` so the top match always paints
    as ``current``.
    """

    def __init__(
        self,
        max_height: int | None = None,
        scroll_offset: int | Callable[[], int] = 0,
        extra_filter: FilterOrBool = True,
        display_arrows: FilterOrBool = False,
        z_index: int = 10**8,
    ) -> None:
        extra_filter = to_filter(extra_filter)
        display_arrows = to_filter(display_arrows)
        super().__init__(
            content=Window(
                content=_HighlightFirstCompletionsMenuControl(),
                width=Dimension(min=8),
                height=Dimension(min=1, max=max_height),
                scroll_offsets=ScrollOffsets(top=scroll_offset, bottom=scroll_offset),
                right_margins=[ScrollbarMargin(display_arrows=display_arrows)],
                dont_extend_width=True,
                style="class:completion-menu",
                z_index=z_index,
            ),
            filter=extra_filter & has_completions & ~is_done,
        )
