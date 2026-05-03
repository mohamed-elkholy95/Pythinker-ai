"""AgentLoop clamps context_window_tokens to provider.get_model_limits().

Mirrors opencode PR #24212: when the model publishes a hard input cap (e.g.
gpt-5.5 at 272k under the Codex OAuth plan), AgentLoop must not let a
configured ``context_window_tokens`` exceed it.
"""

from unittest.mock import MagicMock

from pythinker.agent.loop import AgentLoop
from pythinker.bus.queue import MessageBus
from pythinker.providers.base import GenerationSettings


def _make_provider(model_limits):
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=0)
    provider.get_model_limits = MagicMock(return_value=model_limits)
    return provider


def test_clamps_when_configured_exceeds_input_cap(tmp_path):
    provider = _make_provider({"context": 400_000, "input": 272_000, "output": 128_000})
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="openai-codex/gpt-5.5",
        context_window_tokens=300_000,
    )
    assert loop.context_window_tokens == 272_000


def test_no_clamp_when_within_cap(tmp_path):
    provider = _make_provider({"context": 400_000, "input": 272_000, "output": 128_000})
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="openai-codex/gpt-5.5",
        context_window_tokens=200_000,
    )
    assert loop.context_window_tokens == 200_000


def test_no_clamp_when_provider_returns_none(tmp_path):
    provider = _make_provider(None)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="some-other-model",
        context_window_tokens=500_000,
    )
    assert loop.context_window_tokens == 500_000


def test_hot_reload_also_clamps(tmp_path):
    """Switching to a capped model via hot-reload must re-apply the clamp."""
    from pythinker.providers.factory import ProviderSnapshot

    uncapped = _make_provider(None)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=uncapped,
        workspace=tmp_path,
        model="other-model",
        context_window_tokens=500_000,
    )
    assert loop.context_window_tokens == 500_000

    capped = _make_provider({"context": 400_000, "input": 272_000, "output": 128_000})
    snapshot = ProviderSnapshot(
        provider=capped,
        model="openai-codex/gpt-5.5",
        context_window_tokens=500_000,
        signature=("v2",),
    )
    loop._apply_provider_snapshot(snapshot)
    assert loop.context_window_tokens == 272_000
