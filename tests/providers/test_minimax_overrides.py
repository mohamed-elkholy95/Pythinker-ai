"""MiniMax M2.x sampling-default overrides.

Ports opencode's transform.ts defaults (temperature=1.0, top_p=0.95) into the
MiniMax provider spec so M2 produces well-tuned output without the caller
having to pass these knobs explicitly.
"""

from unittest.mock import patch

import pytest

from pythinker.providers.openai_compat_provider import OpenAICompatProvider
from pythinker.providers.registry import PROVIDERS, find_by_name


def test_minimax_spec_has_m2_overrides():
    spec = next(s for s in PROVIDERS if s.name == "minimax")
    overrides = dict(spec.model_overrides)
    assert "minimax-m2" in overrides
    m2 = overrides["minimax-m2"]
    assert m2["temperature"] == 1.0
    assert m2["top_p"] == 0.95


def test_minimax_anthropic_spec_unchanged_by_overrides():
    """The Anthropic-compatible MiniMax endpoint shouldn't pick up these overrides."""
    spec = next(s for s in PROVIDERS if s.name == "minimax_anthropic")
    assert spec.model_overrides == ()


def _build_kwargs(model: str) -> dict:
    spec = find_by_name("minimax")
    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(api_key="k", default_model=model, spec=spec)
    return provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None, model=model, max_tokens=1024,
        temperature=0.7, reasoning_effort=None, tool_choice=None,
    )


@pytest.mark.parametrize(
    "model",
    ["MiniMax-M2.7", "MiniMax-M2.7-highspeed", "minimax-m2"],
)
def test_minimax_m2_overrides_applied_at_request_time(model: str):
    """Substring match must fire for the canonical (mixed-case) M2 ids."""
    kw = _build_kwargs(model)
    assert kw["temperature"] == 1.0
    assert kw["top_p"] == 0.95


def test_minimax_non_m2_model_does_not_get_overrides():
    """Non-M2 MiniMax models must keep the caller-provided temperature."""
    kw = _build_kwargs("abab6.5-chat")
    assert kw["temperature"] == 0.7
    assert "top_p" not in kw
