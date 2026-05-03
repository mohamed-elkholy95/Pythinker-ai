"""Double-Esc interrupt behaviour for in-flight turns.

The TUI's no-overlay Esc binding has two responsibilities:

* First press arms the interrupt gesture (sets ``state.last_esc_at``
  and surfaces a hint notice). It does NOT cancel the running turn —
  the user might have hit Esc by accident.
* Second press within ``ESC_INTERRUPT_WINDOW_S`` cancels
  ``state.in_flight_task``.

We test the *gesture logic* directly against a stand-in TuiState rather
than the full prompt_toolkit Application — that keeps the test fast and
avoids spinning up a pseudo-tty just to validate timing.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _FakeChatPane:
    notices: list[tuple[str, str]] = field(default_factory=list)

    def append_notice(self, text: str, *, kind: str = "info") -> None:
        self.notices.append((kind, text))


@dataclass
class _FakeApp:
    invalidations: int = 0

    def invalidate(self) -> None:
        self.invalidations += 1


@dataclass
class _FakeState:
    in_flight_task: asyncio.Task | None = None
    last_esc_at: float = 0.0


def _press_esc(state, chat_pane, app, *, window_s: float = 0.8) -> None:
    """Replicates the no-overlay Esc handler from app.py:_build_key_bindings."""
    running = state.in_flight_task and not state.in_flight_task.done()
    if not running:
        return
    now = time.monotonic()
    if now - state.last_esc_at <= window_s:
        state.in_flight_task.cancel()
        state.last_esc_at = 0.0
        chat_pane.append_notice(
            "Turn interrupted (double-Esc). The next message will close "
            "out the cancelled turn.",
            kind="warn",
        )
        app.invalidate()
    else:
        state.last_esc_at = now
        chat_pane.append_notice(
            "Press Esc again within 0.8s to interrupt the agent.",
            kind="info",
        )
        app.invalidate()


async def _long_running() -> None:
    await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# First press only arms; running turn keeps going
# ---------------------------------------------------------------------------


async def test_single_esc_arms_does_not_cancel() -> None:
    state = _FakeState()
    chat_pane = _FakeChatPane()
    app = _FakeApp()
    state.in_flight_task = asyncio.create_task(_long_running())

    _press_esc(state, chat_pane, app)

    assert state.in_flight_task is not None
    assert not state.in_flight_task.done()
    assert state.last_esc_at > 0
    assert app.invalidations == 1
    assert len(chat_pane.notices) == 1
    kind, msg = chat_pane.notices[0]
    assert kind == "info"
    assert "Press Esc again" in msg

    state.in_flight_task.cancel()
    try:
        await state.in_flight_task
    except (asyncio.CancelledError, BaseException):
        pass


# ---------------------------------------------------------------------------
# Second press within window cancels
# ---------------------------------------------------------------------------


async def test_double_esc_within_window_cancels_turn() -> None:
    state = _FakeState()
    chat_pane = _FakeChatPane()
    app = _FakeApp()
    state.in_flight_task = asyncio.create_task(_long_running())

    _press_esc(state, chat_pane, app)
    await asyncio.sleep(0.05)  # well under the 0.8s window
    _press_esc(state, chat_pane, app)
    await asyncio.sleep(0.01)  # let the cancel propagate

    assert state.in_flight_task is not None
    assert state.in_flight_task.cancelled() or state.in_flight_task.done()
    # Arming notice + interrupt notice = exactly two.
    kinds = [k for k, _ in chat_pane.notices]
    assert kinds == ["info", "warn"]
    assert "interrupted" in chat_pane.notices[1][1].lower()
    # last_esc_at gets reset after a successful interrupt so the user
    # can't double-fire by holding Esc.
    assert state.last_esc_at == 0.0


# ---------------------------------------------------------------------------
# Second press after the window starts a fresh arm
# ---------------------------------------------------------------------------


async def test_double_esc_after_window_re_arms_instead_of_cancels() -> None:
    state = _FakeState()
    chat_pane = _FakeChatPane()
    app = _FakeApp()
    state.in_flight_task = asyncio.create_task(_long_running())

    _press_esc(state, chat_pane, app, window_s=0.05)
    await asyncio.sleep(0.1)  # past the (test-shortened) window
    _press_esc(state, chat_pane, app, window_s=0.05)

    # Two arming notices, no cancel.
    kinds = [k for k, _ in chat_pane.notices]
    assert kinds == ["info", "info"]
    assert state.in_flight_task is not None
    assert not state.in_flight_task.done()

    state.in_flight_task.cancel()
    try:
        await state.in_flight_task
    except (asyncio.CancelledError, BaseException):
        pass


# ---------------------------------------------------------------------------
# Idle Esc (no in-flight turn) is a no-op
# ---------------------------------------------------------------------------


async def test_idle_esc_is_noop() -> None:
    state = _FakeState()
    chat_pane = _FakeChatPane()
    app = _FakeApp()

    _press_esc(state, chat_pane, app)

    assert state.last_esc_at == 0.0
    assert chat_pane.notices == []
    assert app.invalidations == 0


# ---------------------------------------------------------------------------
# Done turn (already finished) is a no-op even with Esc spam
# ---------------------------------------------------------------------------


async def test_finished_turn_treats_esc_as_idle() -> None:
    state = _FakeState()
    chat_pane = _FakeChatPane()
    app = _FakeApp()
    state.in_flight_task = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0.01)
    assert state.in_flight_task.done()

    _press_esc(state, chat_pane, app)

    assert chat_pane.notices == []
    assert app.invalidations == 0
