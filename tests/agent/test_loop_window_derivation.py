from unittest.mock import MagicMock

from pythinker.agent.loop import AgentLoop
from pythinker.bus.queue import MessageBus
from pythinker.providers.base import GenerationSettings


def _provider(model_limits: dict[str, int] | None = None) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "openai-codex/gpt-5.5"
    provider.get_model_limits.return_value = model_limits
    provider.generation = GenerationSettings(max_tokens=24_000)
    return provider


def test_unspecified_context_window_picks_profile_input(tmp_path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="openai-codex/gpt-5.5",
        context_window_tokens=None,
    )
    assert loop.context_window_tokens == 272_000


def test_explicit_window_below_cap_kept(tmp_path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="openai-codex/gpt-5.5",
        context_window_tokens=180_000,
    )
    assert loop.context_window_tokens == 180_000


def test_explicit_window_above_cap_clamped(tmp_path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="openai-codex/gpt-5.5",
        context_window_tokens=500_000,
    )
    assert loop.context_window_tokens == 272_000


def test_loop_passes_encoding_to_consolidator(tmp_path):
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="openai-codex/gpt-5.5",
        context_window_tokens=None,
    )
    assert loop.consolidator.encoding == "o200k_base"


def test_loop_unknown_model_consolidator_defaults_to_cl100k(tmp_path):
    p = MagicMock()
    p.get_default_model.return_value = "unknown/v0"
    p.get_model_limits.return_value = None
    p.generation = GenerationSettings(max_tokens=4_096)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=p,
        workspace=tmp_path,
        model="unknown/v0",
        context_window_tokens=10_000,
    )
    assert loop.consolidator.encoding == "cl100k_base"
