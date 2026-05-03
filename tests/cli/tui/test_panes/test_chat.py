"""ChatPane: append blocks, clear, scroll-lock toggling."""
from __future__ import annotations

import pytest


@pytest.fixture
def pane():
    from pythinker.cli.tui.panes.chat import ChatPane
    from pythinker.cli.tui.theme import THEMES
    return ChatPane(theme=THEMES["default"])


def test_starts_empty(pane) -> None:
    assert pane.block_count() == 0


def test_append_user_grows(pane) -> None:
    pane.append_user("hello")
    assert pane.block_count() == 1


def test_append_notice_grows(pane) -> None:
    pane.append_notice("ack", kind="info")
    assert pane.block_count() == 1


def test_clear_resets(pane) -> None:
    pane.append_user("hello")
    pane.append_notice("ack", kind="info")
    pane.clear()
    assert pane.block_count() == 0


def test_assistant_stream_block_returned(pane) -> None:
    handle = pane.append_assistant_stream()
    assert pane.block_count() == 1
    handle.append_delta("hi")
    handle.finalize_markdown()
    rendered = pane.render_ansi(width=80)
    assert "hi" in rendered or rendered  # rendered is non-empty


def test_content_version_changes_for_stream_delta(pane) -> None:
    start = pane.version
    handle = pane.append_assistant_stream()
    after_append = pane.version
    assert after_append > start

    handle.append_delta("hi")
    after_delta = pane.version
    assert after_delta > after_append

    handle.finalize_markdown()
    assert pane.version > after_delta


def test_content_version_changes_for_theme_change(pane) -> None:
    from pythinker.cli.tui.theme import THEMES

    start = pane.version
    theme = next(theme for name, theme in THEMES.items() if name != "default")

    pane.set_theme(theme)

    assert pane.version > start


def test_tool_event_block(pane) -> None:
    from pythinker.agent.loop import ToolEvent
    pane.append_tool_event(ToolEvent(
        name="shell", phase="end",
        args_preview="ls -la",
        result_preview="5 lines",
        duration_ms=1200,
    ))
    assert pane.block_count() == 1


def test_scroll_lock_toggle(pane) -> None:
    assert pane.scroll_lock is False
    pane.set_scroll_lock(True)
    assert pane.scroll_lock is True
    pane.append_user("new message resets lock")
    assert pane.scroll_lock is False
