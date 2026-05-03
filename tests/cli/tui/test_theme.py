"""Theme registry exposes a default + at least one alternative; each entry
yields a prompt_toolkit Style and a Rich Theme."""
from __future__ import annotations

from prompt_toolkit.styles import Style
from rich.theme import Theme

from pythinker.cli.tui.theme import THEMES


def test_default_theme_present() -> None:
    assert "default" in THEMES


def test_themes_have_pt_style_and_rich_theme() -> None:
    for name, theme in THEMES.items():
        assert theme.name == name
        assert isinstance(theme.pt_style, Style)
        assert isinstance(theme.rich_theme, Theme)


def test_at_least_two_themes() -> None:
    assert len(THEMES) >= 2, "ship default + one alternative"
