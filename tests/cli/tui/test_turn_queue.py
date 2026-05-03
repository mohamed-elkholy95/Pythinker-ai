"""Queued TUI turn behavior."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain(rendered: str) -> str:
    return " ".join(_ANSI_RE.sub("", rendered).split())


@pytest.fixture
def state_and_pane():
    from pythinker.cli.tui.app import TuiState
    from pythinker.cli.tui.panes.chat import ChatPane
    from pythinker.cli.tui.theme import THEMES

    state = TuiState(
        session_key="cli:tui",
        model="gpt-4o-mini",
        provider="openai",
        workspace=Path("/tmp"),
    )
    pane = ChatPane(theme=THEMES["default"])
    return state, pane


def test_queue_turn_records_message_and_posts_notice(state_and_pane) -> None:
    from pythinker.cli.tui.app import _queue_turn

    state, pane = state_and_pane

    _queue_turn(state, pane, "second message")
    _queue_turn(state, pane, "third message")

    assert list(state.queued_messages) == ["second message", "third message"]
    rendered = _plain(pane.render_ansi(width=80))
    assert "Message queued (1 pending)" in rendered
    assert "Message queued (2 pending)" in rendered


def test_finish_turn_drains_queued_messages_fifo(state_and_pane) -> None:
    from pythinker.cli.tui.app import _finish_turn_and_get_next

    state, _pane = state_and_pane
    state.waiting = True
    state.queued_messages.extend(["second", "third"])

    assert _finish_turn_and_get_next(state, cancelled=False) == "second"
    assert list(state.queued_messages) == ["third"]
    assert state.waiting is False
    assert _finish_turn_and_get_next(state, cancelled=False) == "third"
    assert list(state.queued_messages) == []


async def test_cancel_in_flight_turn_clears_queue(state_and_pane) -> None:
    from pythinker.cli.tui.app import _cancel_in_flight_turn

    state, pane = state_and_pane

    async def _slow_turn() -> None:
        await asyncio.sleep(60)

    state.in_flight_task = asyncio.create_task(_slow_turn())
    state.queued_messages.extend(["second", "third"])
    await asyncio.sleep(0)

    _cancel_in_flight_turn(
        state,
        pane,
        "Turn cancelled. The next message will close out the interrupted turn.",
    )
    await asyncio.sleep(0)

    assert list(state.queued_messages) == []
    assert state.in_flight_task.cancelled() or state.in_flight_task.done()
    rendered = _plain(pane.render_ansi(width=80))
    assert "Cleared 2 queued messages" in rendered


def test_status_and_hint_show_queue_count(state_and_pane) -> None:
    from pythinker.cli.tui.panes.hint_footer import HintFooter
    from pythinker.cli.tui.panes.overlay import OverlayContainer
    from pythinker.cli.tui.panes.status_bar import StatusBar

    state, _pane = state_and_pane
    state.waiting = True
    state.queued_messages.extend(["second", "third"])

    status_plain = "".join(text for _style, text in StatusBar(state).render())
    hint_plain = "".join(
        text for _style, text in HintFooter(state, OverlayContainer()).render()
    )

    assert "queue 2" in status_plain
    assert "2 queued" in hint_plain
