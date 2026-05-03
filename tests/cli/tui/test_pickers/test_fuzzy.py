"""fuzzy_match: case-insensitive substring scoring with the tie-break order
prefix > position > length > alphabetical."""
from __future__ import annotations


def test_no_query_returns_input_order_unchanged() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["banana", "apple", "cherry"]
    assert [name for name, _ in fuzzy_match("", items)] == items


def test_query_filters_non_matches() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["gpt-4o-mini", "claude-sonnet", "gemini-pro"]
    matches = [name for name, _ in fuzzy_match("gpt", items)]
    assert matches == ["gpt-4o-mini"]


def test_prefix_wins_over_substring() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["super-claude", "claude-sonnet"]
    matches = [name for name, _ in fuzzy_match("claude", items)]
    assert matches == ["claude-sonnet", "super-claude"]


def test_earlier_position_wins_when_neither_is_prefix() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["xx-claude-yy", "x-claude-yy"]
    matches = [name for name, _ in fuzzy_match("claude", items)]
    assert matches == ["x-claude-yy", "xx-claude-yy"]


def test_shorter_wins_when_position_ties() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["claude-sonnet-extended", "claude-sonnet"]
    matches = [name for name, _ in fuzzy_match("claude", items)]
    assert matches == ["claude-sonnet", "claude-sonnet-extended"]


def test_alphabetical_tie_break_when_all_else_equal() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["claude-b", "claude-a"]
    matches = [name for name, _ in fuzzy_match("claude", items)]
    assert matches == ["claude-a", "claude-b"]


def test_case_insensitive() -> None:
    from pythinker.cli.tui.pickers.fuzzy import fuzzy_match
    items = ["Claude-Sonnet"]
    assert [name for name, _ in fuzzy_match("CLAUDE", items)] == ["Claude-Sonnet"]


def test_picker_screen_filter_and_select() -> None:
    from pythinker.cli.tui.pickers.fuzzy import FuzzyPickerScreen
    selected: list[str] = []

    async def _on_select(item: str) -> None:
        selected.append(item)

    items = ["apple", "banana", "cherry"]
    screen = FuzzyPickerScreen(items=items, label_fn=str, on_select=_on_select)

    screen.set_query("ban")
    assert [it for it, _ in screen.visible_items()] == ["banana"]

    screen.set_query("")
    screen.move_cursor(+1)            # banana
    import asyncio
    asyncio.run(screen.commit())
    assert selected == ["banana"]
