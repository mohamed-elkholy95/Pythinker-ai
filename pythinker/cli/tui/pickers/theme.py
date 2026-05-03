"""/theme picker.

This is the one picker that persists immediately to disk: writes
config.cli.tui.theme via config.loader.save_config so the choice survives
the next pythinker tui invocation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythinker.cli.tui.pickers.fuzzy import FuzzyPickerScreen
from pythinker.cli.tui.theme import THEMES

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


async def open_theme_picker(app: "TuiApp") -> None:
    items = list(THEMES.values())

    async def _on_select(theme) -> None:
        app.application.style = theme.pt_style
        app.chat_pane.set_theme(theme)
        app.state.theme_name = theme.name
        try:
            from pythinker.config.loader import get_config_path, save_config
            cfg = app.config
            cfg.cli.tui.theme = theme.name
            save_config(cfg, get_config_path())
            app.chat_pane.append_notice(f"theme → {theme.name} (saved)", kind="info")
        except Exception as e:
            app.chat_pane.append_notice(
                f"theme persisted in-memory; save failed: {e}", kind="warn"
            )
        finally:
            app.overlay.pop()
            app.application.invalidate()

    app.overlay.push(FuzzyPickerScreen(
        items=items, label_fn=lambda t: t.name, on_select=_on_select, title="theme",
    ))
