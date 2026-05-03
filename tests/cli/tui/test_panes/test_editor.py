"""EditorPane slash-command completion behavior."""
from __future__ import annotations


def test_slash_completion_reopens_after_delete(monkeypatch) -> None:
    from pythinker.cli.tui.panes.editor import EditorPane

    async def _submit(text: str) -> None:
        raise AssertionError(f"unexpected submit: {text}")

    pane = EditorPane(_submit)
    calls: list[dict[str, object]] = []

    def _start_completion(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(pane.buffer, "start_completion", _start_completion)

    pane.buffer.text = "/mode"
    pane.buffer.cursor_position = len(pane.buffer.text)
    pane._refresh_slash_completion(pane.buffer)

    assert calls == [{"select_first": False}]
