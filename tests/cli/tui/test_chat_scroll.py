"""Tests for the chat-region wheel-scroll fix.

Mouse wheel events used to be silently undone within ~80 ms by the
spinner ticker's invalidate cadence: ``Window._scroll_to_make_cursor_visible``
saw the synthetic cursor parked on the last line and snapped
``vertical_scroll`` back to the bottom on every render. The fix keeps
``ChatPane.user_scroll`` and the concrete ``Window.vertical_scroll`` in
sync so wrapped prompt_toolkit windows repaint immediately. These tests
pin the state-machine and scroll callback so a regression turns into a
failing assertion instead of a silent UX bug.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.containers import FloatContainer, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl

from pythinker.cli.tui.layout import ChatScrollWindow, _chat_get_vertical_scroll, build_layout
from pythinker.cli.tui.panes.chat import ChatPane
from pythinker.cli.tui.theme import THEMES


@pytest.fixture
def pane() -> ChatPane:
    return ChatPane(theme=THEMES["default"])


def _fake_window(content: int, viewport: int) -> Window:
    """Stand-in for prompt_toolkit's Window with just the attributes
    ``_chat_get_vertical_scroll`` reads."""
    return cast(
        Window,
        cast(
            object,
            SimpleNamespace(
                render_info=SimpleNamespace(content_height=content, window_height=viewport)
            ),
        ),
    )


def _chat_window_from_layout(layout: Layout) -> ChatScrollWindow:
    container = cast(FloatContainer, layout.container)
    assert isinstance(container, FloatContainer)
    body = cast(HSplit, container.content)
    assert isinstance(body, HSplit)
    chat_windows = [child for child in body.children if isinstance(child, ChatScrollWindow)]
    assert len(chat_windows) == 1
    return chat_windows[0]


def test_default_chat_pane_follows_bottom(pane: ChatPane) -> None:
    """A fresh ChatPane has no user offset — the resolver auto-pins to
    the bottom of the content."""
    assert pane.user_scroll is None
    assert pane.scroll_lock is False
    resolve = _chat_get_vertical_scroll(pane)
    # Content 200 rows, viewport 50 → bottom is at offset 150.
    assert resolve(_fake_window(200, 50)) == 150


def test_chat_window_does_not_expand_past_rendered_transcript(pane: ChatPane) -> None:
    """The chat region must stay snug on startup and after the first turn.

    A preferred height alone is not enough in prompt_toolkit: HSplit may
    stretch a Window into leftover space unless ``dont_extend_height`` is
    set. When that happened, the first user message sat at the top of a
    huge empty chat viewport instead of keeping the editor directly below
    the transcript.
    """
    layout = build_layout(
        status_bar=SimpleNamespace(control=FormattedTextControl("status")),
        chat_control=FormattedTextControl("▌ you\nhi\n"),
        editor_control=FormattedTextControl(""),
        hint_footer=SimpleNamespace(control=FormattedTextControl("hint")),
        overlay_control=FormattedTextControl(""),
        overlay_visible=lambda: False,
        chat_height=lambda: 3,
        chat_pane=pane,
    )

    chat_window = _chat_window_from_layout(layout)
    assert chat_window.dont_extend_height()


def test_user_scroll_offset_is_clamped_into_range(pane: ChatPane) -> None:
    pane.set_user_scroll(40)
    assert pane.user_scroll == 40
    assert pane.scroll_lock is True

    resolve = _chat_get_vertical_scroll(pane)
    # Within range: returned exactly.
    assert resolve(_fake_window(200, 50)) == 40
    # Above max: clamped to (content - viewport).
    pane.set_user_scroll(500)
    assert resolve(_fake_window(200, 50)) == 150
    # Negative: ChatPane normalises to 0 on set; resolver also floors.
    pane.set_user_scroll(-10)
    assert pane.user_scroll == 0
    assert resolve(_fake_window(200, 50)) == 0


def test_scroll_by_rows_anchors_first_scroll_at_real_max_scroll(pane: ChatPane) -> None:
    """First PageUp/mouse-wheel-up must start from content-height - viewport.

    A previous keybinding used ``len(rendered_lines) - 1`` as the bottom
    offset. With content=200 and viewport=50 that anchored at 199, so the
    first PageUp became 189 and was clamped back to max_scroll=150 — no
    visible movement. ChatScrollWindow uses render_info and lands on 140.
    """
    win = ChatScrollWindow(
        chat_pane=pane,
        content=FormattedTextControl(""),
    )
    win.render_info = SimpleNamespace(content_height=200, window_height=50)

    win.scroll_by_rows(-10)

    assert pane.user_scroll == 140
    assert pane.scroll_lock is True
    assert win.vertical_scroll == 140
    assert win.vertical_scroll_2 == 0


def test_scroll_by_rows_releases_lock_and_syncs_window_at_bottom(pane: ChatPane) -> None:
    win = ChatScrollWindow(
        chat_pane=pane,
        content=FormattedTextControl(""),
    )
    win.render_info = SimpleNamespace(content_height=200, window_height=50)
    pane.set_user_scroll(140)
    win.vertical_scroll = 140
    win.vertical_scroll_2 = 3

    win.scroll_by_rows(10)

    assert pane.user_scroll is None
    assert pane.scroll_lock is False
    assert win.vertical_scroll == 150
    assert win.vertical_scroll_2 == 0


def test_scroll_by_rows_clears_stale_lock_when_content_fits(pane: ChatPane) -> None:
    win = ChatScrollWindow(
        chat_pane=pane,
        content=FormattedTextControl(""),
    )
    win.render_info = SimpleNamespace(content_height=20, window_height=50)
    pane.set_user_scroll(10)
    win.vertical_scroll = 10

    win.scroll_by_rows(-10)

    assert pane.user_scroll is None
    assert pane.scroll_lock is False
    assert win.vertical_scroll == 0
    assert win.vertical_scroll_2 == 0


def test_wheel_step_scales_with_viewport_height(pane: ChatPane) -> None:
    win = ChatScrollWindow(
        chat_pane=pane,
        content=FormattedTextControl(""),
    )
    assert win._wheel_step_rows() == 3

    win.render_info = SimpleNamespace(content_height=200, window_height=50)
    assert win._wheel_step_rows() == 10

    win.render_info = SimpleNamespace(content_height=400, window_height=120)
    assert win._wheel_step_rows() == 12


def test_clearing_user_scroll_releases_the_lock(pane: ChatPane) -> None:
    pane.set_user_scroll(20)
    assert pane.scroll_lock is True
    pane.set_user_scroll(None)
    assert pane.user_scroll is None
    assert pane.scroll_lock is False


def test_set_scroll_lock_false_also_clears_offset(pane: ChatPane) -> None:
    """``set_scroll_lock(False)`` is the public End-key path — it must
    drop the user offset so the next render snaps back to streaming."""
    pane.set_user_scroll(30)
    pane.set_scroll_lock(False)
    assert pane.user_scroll is None
    assert pane.scroll_lock is False


def test_appending_user_message_releases_the_lock(pane: ChatPane) -> None:
    """Sending a new turn must always re-pin to the latest output —
    otherwise the user would type a message and then not see the reply
    because the window is still parked at an old scroll offset."""
    pane.set_user_scroll(20)
    pane.append_user("hello")
    assert pane.user_scroll is None
    assert pane.scroll_lock is False


def test_resolver_handles_missing_render_info(pane: ChatPane) -> None:
    """First render: render_info is None — resolver must return 0 instead
    of raising."""
    resolve = _chat_get_vertical_scroll(pane)
    assert resolve(cast(Window, cast(object, SimpleNamespace(render_info=None)))) == 0


def test_resolver_returns_zero_when_content_fits_viewport(pane: ChatPane) -> None:
    """Short transcript: nothing to scroll, max offset is 0."""
    resolve = _chat_get_vertical_scroll(pane)
    assert resolve(_fake_window(content=10, viewport=50)) == 0
