"""StatusBar: render state fragments."""
from __future__ import annotations


def test_status_bar_renders_state_fragments() -> None:
    from pathlib import Path

    from pythinker.cli.tui.app import TuiState
    from pythinker.cli.tui.panes.status_bar import StatusBar

    state = TuiState(
        session_key="cli:tui",
        model="gpt-4o-mini",
        provider="openai",
        workspace=Path("/tmp"),
        waiting=False,
        waiting_started_at=None,
        last_turn_tokens=12438,
        theme_name="default",
        in_flight_task=None,
    )
    bar = StatusBar(state)
    fragments = bar.render()
    plain = "".join(text for _style, text in fragments)
    assert "Pythinker" in plain
    assert "provider openai" in plain
    assert "gpt-4o-mini" in plain
    assert "cli:tui" in plain
    assert "tokens 12,438 last" in plain


def test_status_bar_keeps_waiting_dot_separate_from_brand() -> None:
    from pathlib import Path

    from pythinker.cli.tui.app import TuiState
    from pythinker.cli.tui.panes.status_bar import StatusBar

    state = TuiState(
        session_key="cli:tui",
        model="gpt-4o-mini",
        provider="openai",
        workspace=Path("/tmp"),
        waiting=True,
    )
    fragments = StatusBar(state).render()

    assert any(style == "class:status.dot.active" for style, _text in fragments)
    brand_index = next(
        index for index, (style, _text) in enumerate(fragments)
        if style == "class:status.brand"
    )
    assert fragments[brand_index][1] == " Pythinker"
    assert fragments[brand_index - 1][0] == "class:status.dot.active"
