"""Codex-style single-glyph waiting spinner.

A single rotating star-like glyph cycles through ``✦ ✶ ✴ ✷`` while the
agent works, followed by a ``Generating… (Xs · ↓ Y tokens)`` caption on
the same line. The whole spinner is one row tall and ~one terminal cell
wide for the glyph itself — roughly 4×4 mm at default terminal font
size. The animation frame is computed from ``time.monotonic()`` so the
visual advances on every render — the application just needs to
invalidate periodically while ``state.waiting`` is True (see
``app.py``'s ticker).

Design reference: the Codex CLI ``* Generating… (25s · ↓ 532 tokens)``
status line.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from prompt_toolkit.layout.controls import FormattedTextControl

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiState


class WaitingSpinner:
    """Single-glyph rotating spinner inline with a status caption.

    Uses the canonical 10-frame Braille "dots" spinner from
    cli-spinners/ora — designed to read as a smooth rotation because each
    successive frame shares 5 of 6 lit dots with its neighbour. At 12 Hz
    that's an ~83 ms per-frame budget, which is the perceptual sweet
    spot for "moving" without "buzzing".
    """

    # Each glyph is a single Braille cell where the lit dots rotate one
    # position clockwise per frame. 10 frames gives a full revolution
    # plus two intermediate phases for visual smoothness.
    GLYPHS: tuple[str, ...] = (
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
    )
    FRAME_HZ = 12
    # Approx tokens-per-character for the live counter; exact tokenization
    # only happens after the turn ends via the provider's usage report.
    _CHARS_PER_TOKEN = 4

    def __init__(self, state: "TuiState") -> None:
        self._state = state
        self._control = FormattedTextControl(self.render)

    @property
    def control(self) -> FormattedTextControl:
        return self._control

    @property
    def height(self) -> int:
        return 1

    def render(self) -> list[tuple[str, str]]:
        if not self._state.waiting:
            return []

        frame = int(time.monotonic() * self.FRAME_HZ) % len(self.GLYPHS)
        glyph = self.GLYPHS[frame]

        elapsed = 0
        if self._state.waiting_started_at:
            elapsed = max(0, int(time.monotonic() - self._state.waiting_started_at))

        streamed = max(0, getattr(self._state, "streamed_chars", 0))
        approx_tokens = streamed // self._CHARS_PER_TOKEN

        # Inline format mirrors the Codex CLI:
        #   ✦ Generating… (25s · ↓ 532 tokens)
        if approx_tokens > 0:
            tail = f" Generating… ({elapsed}s · ↓ {approx_tokens} tokens)"
        else:
            tail = f" Generating… ({elapsed}s)"

        return [
            ("", " "),
            ("class:spinner.head", glyph),
            ("class:spinner.caption", tail),
            ("", "\n"),
        ]
