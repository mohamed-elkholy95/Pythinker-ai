"""AssistantStreamHandle: bridge AgentLoop streaming callbacks into the
ChatPane's open assistant block."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pythinker.cli.tui.panes.chat import AssistantBlockHandle as PaneHandle
    from pythinker.cli.tui.panes.chat import ChatPane


class AssistantStreamHandle:
    def __init__(
        self,
        chat_pane: "ChatPane",
        app: Any,
        *,
        debounce_seconds: float = 0.15,
        state: Any | None = None,
    ) -> None:
        self._pane = chat_pane
        self._app = app
        self._debounce = debounce_seconds
        self._handle: "PaneHandle | None" = None
        self._last = 0.0
        # Optional reference to TuiState. When wired, every delta bumps
        # `state.streamed_chars` so the WaitingSpinner can show a live
        # approx-token counter — exact tokenization waits for `usage`.
        self._state = state
        # Sticky flag — set on the first delta, never cleared (even after
        # on_end nulls _handle). The non-streaming fallback in app.py
        # checks .started to decide whether to paint resp.content; without
        # this, _handle being None after on_end made the fallback fire and
        # produced a duplicate assistant block on every turn.
        self.started: bool = False

    async def on_delta(self, delta: str) -> None:
        if self._handle is None:
            self._handle = self._pane.append_assistant_stream()
            self._last = time.monotonic()
            self.started = True
        self._handle.append_delta(delta)
        if self._state is not None:
            try:
                self._state.streamed_chars += len(delta)
            except Exception:  # noqa: BLE001 — tolerate odd state shapes in tests
                pass
        if time.monotonic() - self._last > self._debounce:
            self._app.invalidate()
            self._last = time.monotonic()

    async def on_progress(self, content: str, *, tool_hint: bool = False) -> None:
        if tool_hint:
            return  # replaced by on_tool_event
        # Status-bar caption is owned by the App; this hook is a no-op here.
        return

    async def on_end(self, *, resuming: bool = False) -> None:
        if self._handle is not None:
            self._handle.finalize_markdown()
            self._handle = None
        self._app.invalidate()
        if resuming:
            self._last = 0.0
