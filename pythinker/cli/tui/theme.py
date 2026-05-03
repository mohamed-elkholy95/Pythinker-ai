"""TUI theme registry. Each theme bundles a prompt_toolkit Style for layout
chrome (status bar, hint footer, separator) and a Rich Theme used when
rendering chat content into ANSI."""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.styles import Style
from rich.theme import Theme


@dataclass(frozen=True)
class TuiTheme:
    name: str
    pt_style: Style
    rich_theme: Theme


_DEFAULT_PT = Style.from_dict({
    "status": "bg:#1e1e2e #cdd6f4",
    "status.brand": "bg:#1e1e2e #12b76a bold",
    "status.dot.active": "bg:#1e1e2e #12b76a bold",
    "status.dot.idle": "bg:#1e1e2e #6c7086",
    "status.label": "bg:#1e1e2e #7f849c",
    "status.value": "bg:#1e1e2e #cdd6f4",
    "status.sep": "bg:#1e1e2e #45475a",
    "status.frame": "#313744",
    "status.gap": "bg:default",
    "rule": "#45475a",
    "hint": "#6c7086",
    "editor.prompt": "#C0C0C0 bold",
    "editor.placeholder": "#6c7086 italic",
    "editor": "bg:#121720 #E8E3D5",
    # Silver palette — bright C0C0C0 head with a slightly dimmer tail so
    # multi-frame glyphs read as a metallic shimmer rather than flat white.
    "spinner.head": "#D8D8D8 bold",
    "spinner.tail": "#A8A8A8",
    "spinner.dim": "#3C414B",
    "spinner.caption": "#A8A8A8 italic",
    "spinner": "bg:default",
    # Modal overlay (picker / help / status) needs a solid background so the
    # chat under it stays hidden — otherwise text bleeds through and looks
    # "overlapped" or half-visible.
    "overlay": "bg:#11151B #E8E3D5",
    "overlay.frame": "#C0C0C0",
    "overlay.frame.border": "#C0C0C0",
    "picker.title": "bg:#C0C0C0 #11151B bold",
    "picker.meta": "#7B7F87",
    "picker.prompt": "#A8A8A8 bold",
    "picker.query": "#E8E3D5 bold",
    "picker.query.placeholder": "#6c7086 italic",
    "picker.rule": "#313744",
    "picker.row": "#D9D4C7",
    "picker.selected": "bg:#263241 #F7D08A bold",
    "picker.footer": "#7B7F87 italic",
    "tool.error": "#f38ba8",
    "notice.info": "#94e2d5",
    "notice.warn": "#f9e2af",
    "notice.error": "#f38ba8",
    # Right-edge scrollbar on the chat window. Silver thumb on a faint
    # track so it shows up only when there's overflow (prompt_toolkit
    # paints the track with whatever character the terminal renders for
    # ' ' on the bg color, giving a subtle vertical strip).
    "scrollbar.background": "bg:#1a1f29",
    "scrollbar.button": "bg:#C0C0C0",
})

_DEFAULT_RICH = Theme({
    "user.role": "bold #89b4fa",
    "assistant.role": "bold #94e2d5",
    "tool.name": "bold #f9e2af",
    "tool.preview": "#bac2de",
    "tool.error": "#f38ba8",
    "timestamp": "dim",
    "interrupted": "dim italic",
})


_MONO_PT = Style.from_dict({
    "status": "bg:#000000 #ffffff",
    "status.brand": "bg:#000000 #ffffff bold",
    "status.dot.active": "bg:#000000 #ffffff bold",
    "status.dot.idle": "bg:#000000 #888888",
    "status.label": "bg:#000000 #888888",
    "status.value": "bg:#000000 #ffffff",
    "status.sep": "bg:#000000 #888888",
    "status.frame": "#444444",
    "status.gap": "bg:default",
    "rule": "#444444",
    "hint": "#888888",
    "editor": "bg:#000000 #ffffff",
    "editor.prompt": "#ffffff bold",
    "editor.placeholder": "#888888 italic",
    "overlay": "bg:#000000 #ffffff",
    "overlay.frame": "#ffffff",
    "overlay.frame.border": "#ffffff",
    "picker.title": "bg:#ffffff #000000 bold",
    "picker.meta": "#888888",
    "picker.prompt": "#ffffff bold",
    "picker.query": "#ffffff bold",
    "picker.query.placeholder": "#888888 italic",
    "picker.rule": "#444444",
    "picker.row": "#ffffff",
    "picker.selected": "bg:#ffffff #000000 bold",
    "picker.footer": "#888888 italic",
    "tool.error": "#ffffff bold",
    "notice.info": "#ffffff",
    "notice.warn": "#ffffff bold",
    "notice.error": "#ffffff bold reverse",
    "scrollbar.background": "bg:#222222",
    "scrollbar.button": "bg:#ffffff",
})

_MONO_RICH = Theme({
    "user.role": "bold",
    "assistant.role": "bold",
    "tool.name": "bold",
    "tool.preview": "default",
    "tool.error": "bold reverse",
    "timestamp": "dim",
    "interrupted": "dim italic",
})


THEMES: dict[str, TuiTheme] = {
    "default": TuiTheme(
        name="default",
        pt_style=_DEFAULT_PT,
        rich_theme=_DEFAULT_RICH,
    ),
    "monochrome": TuiTheme(
        name="monochrome",
        pt_style=_MONO_PT,
        rich_theme=_MONO_RICH,
    ),
}
