"""/sessions picker: list known sessions, switch on select."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythinker.cli.tui.pickers.fuzzy import FuzzyPickerScreen

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


def _trunc(s: str, n: int) -> str:
    """Hard-truncate ``s`` to ``n`` chars with a trailing ellipsis if clipped."""
    return s if len(s) <= n else s[:n - 1] + "…"


def _label(item: dict) -> str:
    key = item.get("key") or item.get("session_key") or "?"
    n = item.get("message_count") or item.get("messages") or 0
    last = item.get("last_active") or item.get("updated_at") or ""
    return f"{_trunc(key, 30):30s}  msgs={n:<5}  {last}"


async def open_sessions_picker(app: "TuiApp") -> None:
    sessions = list(app.agent_loop.sessions.list_sessions())

    async def _on_select(item: dict) -> None:
        new_key = item.get("key") or item.get("session_key")
        if not new_key:
            return
        app.state.session_key = new_key
        sess = app.agent_loop.sessions.get_or_create(new_key)
        history = sess.get_history(max_messages=200)
        app.chat_pane.reload_from_history(history)
        app.status_bar.refresh()
        app.overlay.pop()
        app.application.invalidate()

    app.overlay.push(FuzzyPickerScreen(
        items=sessions, label_fn=_label, on_select=_on_select,
        title="sessions",
    ))
