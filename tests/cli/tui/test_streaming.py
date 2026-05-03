"""AssistantStreamHandle accumulates deltas, debounces re-renders, and
swaps to markdown on stream end."""
from __future__ import annotations

import pytest


@pytest.fixture
def chat_with_app(monkeypatch):
    from pythinker.cli.tui.panes.chat import ChatPane
    from pythinker.cli.tui.theme import THEMES

    invalidations: list[int] = []

    class _AppStub:
        output = type(
            "O", (), {"get_size": staticmethod(lambda: type("S", (), {"columns": 80})())}
        )()

        def invalidate(self) -> None:
            invalidations.append(1)

    pane = ChatPane(theme=THEMES["default"])
    return pane, _AppStub(), invalidations


async def test_delta_accumulates_and_finalizes_markdown(chat_with_app):
    from pythinker.cli.tui.streaming import AssistantStreamHandle

    pane, app, _ = chat_with_app
    handle = AssistantStreamHandle(pane, app, debounce_seconds=0.0)
    await handle.on_delta("hello ")
    await handle.on_delta("world")
    await handle.on_end()
    rendered = pane.render_ansi(width=80)
    assert "hello world" in rendered


async def test_debounce_throttles_invalidation(chat_with_app):
    from pythinker.cli.tui.streaming import AssistantStreamHandle

    pane, app, invalidations = chat_with_app
    handle = AssistantStreamHandle(pane, app, debounce_seconds=10.0)  # huge: skip all middle invalidations
    for c in "abcdefg":
        await handle.on_delta(c)
    # No invalidations during streaming, since debounce window has not elapsed.
    assert len(invalidations) == 0
    await handle.on_end()
    # Final flush does invalidate exactly once.
    assert len(invalidations) == 1
