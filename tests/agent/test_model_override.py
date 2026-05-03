"""When ``session.metadata['model_override']`` is set, ``AgentLoop._run_agent_loop``
must pass that value to ``AgentRunSpec.model``. When it's absent, ``AgentRunSpec.model``
falls back to ``AgentLoop.model``.

Phase 3 Task 8: per-chat model override is stored on the session by the
WebSocket channel's ``set_model`` envelope handler. The agent loop is the
final consumer — this test pins the contract so accidental regressions
(e.g. someone refactoring run-spec construction back to ``self.model``)
fail loudly.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pythinker.agent.loop import AgentLoop
from pythinker.agent.runner import AgentRunResult
from pythinker.bus.queue import MessageBus


def _make_loop(tmp_path: Path, default_model: str) -> AgentLoop:
    """Minimal AgentLoop wired with a stub provider/bus.

    Mirrors the bare construction pattern used in ``test_loop_save_turn.py``
    and ``test_auto_compact.py`` — workspace = tmp_path, MessageBus, MagicMock
    provider. We do not exercise the network, so a stub provider suffices.
    """
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model=default_model,
    )


async def _capture_run_spec(loop: AgentLoop, session) -> object:
    """Drive ``_run_agent_loop`` once and return the AgentRunSpec it built."""
    captured: dict[str, object] = {}

    async def fake_run(spec):
        captured["spec"] = spec
        return AgentRunResult(
            final_content="ok",
            messages=[],
            tools_used=[],
            stop_reason="finished",
            had_injections=False,
            usage={},
        )

    loop.runner.run = fake_run  # type: ignore[method-assign]
    await loop._run_agent_loop(
        initial_messages=[{"role": "user", "content": "hi"}],
        session=session,
        channel="websocket",
        chat_id="abcd",
    )
    return captured["spec"]


@pytest.mark.asyncio
async def test_run_spec_uses_default_model_when_no_override(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, default_model="anthropic/claude-3-5-sonnet-20241022")
    session = loop.sessions.get_or_create("websocket:abcd")
    spec = await _capture_run_spec(loop, session)
    assert spec.model == "anthropic/claude-3-5-sonnet-20241022"


@pytest.mark.asyncio
async def test_run_spec_uses_override_when_set(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, default_model="anthropic/claude-3-5-sonnet-20241022")
    session = loop.sessions.get_or_create("websocket:abcd")
    session.metadata["model_override"] = "anthropic/claude-3-5-haiku-20241022"
    spec = await _capture_run_spec(loop, session)
    assert spec.model == "anthropic/claude-3-5-haiku-20241022"


@pytest.mark.asyncio
async def test_run_spec_ignores_blank_override(tmp_path: Path) -> None:
    """An empty/whitespace override must fall back to the default — guards
    against a UI bug clearing the override to '' instead of deleting the key."""
    loop = _make_loop(tmp_path, default_model="anthropic/claude-3-5-sonnet-20241022")
    session = loop.sessions.get_or_create("websocket:abcd")
    session.metadata["model_override"] = "   "
    spec = await _capture_run_spec(loop, session)
    assert spec.model == "anthropic/claude-3-5-sonnet-20241022"


@pytest.mark.asyncio
async def test_run_spec_ignores_non_string_override(tmp_path: Path) -> None:
    """A non-string override (corrupt metadata) must fall back, not crash."""
    loop = _make_loop(tmp_path, default_model="anthropic/claude-3-5-sonnet-20241022")
    session = loop.sessions.get_or_create("websocket:abcd")
    session.metadata["model_override"] = 12345  # type: ignore[assignment]
    spec = await _capture_run_spec(loop, session)
    assert spec.model == "anthropic/claude-3-5-sonnet-20241022"
