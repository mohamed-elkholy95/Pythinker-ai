"""Read-only snapshot dict used by /status overlay."""

from __future__ import annotations

from typing import Any


def collect_status_snapshot(app: Any) -> dict:
    state = app.state
    return {
        "session_key": state.session_key,
        "model": state.model,
        "provider": state.provider,
        "workspace": str(state.workspace),
        "theme_name": state.theme_name,
        "last_turn_tokens": state.last_turn_tokens,
        "waiting": state.waiting,
    }
