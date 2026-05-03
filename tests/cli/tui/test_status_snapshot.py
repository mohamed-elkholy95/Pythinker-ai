from __future__ import annotations


def test_status_snapshot_has_required_keys() -> None:
    from pathlib import Path

    from pythinker.cli.tui.app import TuiState
    from pythinker.cli.tui.status_snapshot import collect_status_snapshot

    state = TuiState(
        session_key="cli:tui",
        model="gpt-4o-mini",
        provider="openai",
        workspace=Path("/tmp"),
        waiting=False,
        waiting_started_at=None,
        last_turn_tokens=0,
        theme_name="default",
        in_flight_task=None,
    )

    class _StubApp:
        config = None
        state = None
        agent_loop = None

    app = _StubApp()
    app.state = state
    snap = collect_status_snapshot(app)
    for key in (
        "session_key",
        "model",
        "provider",
        "workspace",
        "theme_name",
        "last_turn_tokens",
    ):
        assert key in snap
