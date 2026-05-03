"""Provider factory: single source of truth for LLM provider construction.

Every entrypoint (SDK facade, `pythinker serve`, `pythinker gateway`,
interactive CLI) must route through `make_provider` so a config change
can be detected, hot-reloaded, or audited from one place.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pythinker.config.schema import Config
from pythinker.providers.factory import (
    ProviderSnapshot,
    build_provider_snapshot,
    make_provider,
    provider_signature,
)


def _config(model: str, *, provider: str = "auto", **provider_kwargs) -> Config:
    cfg = Config()
    cfg.agents.defaults.model = model
    cfg.agents.defaults.provider = provider
    if provider_kwargs:
        # Apply each provider section, e.g. openai={"apiKey": "..."}
        for prov_name, kv in provider_kwargs.items():
            getattr(cfg.providers, prov_name).api_key = kv.get("apiKey", "")
    return cfg


def test_make_provider_routes_openai_codex_to_oauth_backend():
    cfg = _config("openai-codex/gpt-5.5")
    provider = make_provider(cfg)
    assert provider.__class__.__name__ == "OpenAICodexProvider"


def test_make_provider_routes_github_copilot_to_oauth_backend():
    cfg = _config("github-copilot/gpt-4.1")
    provider = make_provider(cfg)
    assert provider.__class__.__name__ == "GitHubCopilotProvider"


def test_make_provider_routes_openai_compat_when_key_set():
    cfg = _config("openai/gpt-4.1", openai={"apiKey": "sk-test"})
    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = make_provider(cfg)
    assert provider.__class__.__name__ == "OpenAICompatProvider"


def test_make_provider_raises_on_missing_key_for_keyed_backend():
    """Validation moved to factory; CLI/SDK both see the same ValueError."""
    cfg = _config("openai/gpt-4.1")  # no key set
    with pytest.raises(ValueError, match="No API key configured"):
        make_provider(cfg)


def test_make_provider_raises_on_missing_azure_credentials():
    cfg = _config("azure-openai/my-deployment", provider="azure_openai")
    with pytest.raises(ValueError, match="Azure OpenAI requires"):
        make_provider(cfg)


def test_make_provider_routes_minimax_via_model_prefix():
    cfg = _config("minimax/MiniMax-M2", minimax={"apiKey": "test"})
    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = make_provider(cfg)
    assert provider.__class__.__name__ == "OpenAICompatProvider"


def test_make_provider_skips_key_check_for_oauth_providers():
    """OAuth providers (codex, copilot) must not require an API key."""
    cfg = _config("openai-codex/gpt-5.5")
    # Should not raise — OAuth backends are exempt from key validation.
    provider = make_provider(cfg)
    assert provider is not None


def test_provider_signature_changes_when_model_changes():
    base = _config("openai/gpt-4.1", openai={"apiKey": "sk"})
    other = _config("openai/gpt-4o", openai={"apiKey": "sk"})
    assert provider_signature(base) != provider_signature(other)


def test_provider_signature_stable_across_unrelated_field_changes():
    """Workspace/timezone/disabled_skills don't affect provider identity."""
    a = _config("openai/gpt-4.1", openai={"apiKey": "sk"})
    b = _config("openai/gpt-4.1", openai={"apiKey": "sk"})
    b.agents.defaults.timezone = "Asia/Shanghai"
    b.agents.defaults.disabled_skills = ["weather"]
    assert provider_signature(a) == provider_signature(b)


def test_provider_signature_changes_when_api_key_changes():
    """Key rotation must invalidate the snapshot."""
    a = _config("openai/gpt-4.1", openai={"apiKey": "sk-old"})
    b = _config("openai/gpt-4.1", openai={"apiKey": "sk-new"})
    assert provider_signature(a) != provider_signature(b)


def test_provider_signature_changes_when_extra_body_changes():
    """Edits to provider extra_body must invalidate the hot-reload signature."""
    cfg = _config("openai/gpt-4.1", openai={"apiKey": "sk"})
    cfg.providers.openai.extra_body = {"x": 1}
    sig1 = provider_signature(cfg)
    cfg.providers.openai.extra_body = {"x": 2}
    sig2 = provider_signature(cfg)
    assert sig1 != sig2


def test_provider_signature_changes_when_extra_headers_changes():
    """Edits to provider extra_headers must invalidate the hot-reload signature."""
    cfg = _config("openai/gpt-4.1", openai={"apiKey": "sk"})
    cfg.providers.openai.extra_headers = {"X-App": "a"}
    sig1 = provider_signature(cfg)
    cfg.providers.openai.extra_headers = {"X-App": "b"}
    sig2 = provider_signature(cfg)
    assert sig1 != sig2


def test_build_provider_snapshot_carries_provider_and_signature():
    cfg = _config("openai/gpt-4.1", openai={"apiKey": "sk-test"})
    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        snap = build_provider_snapshot(cfg)
    assert isinstance(snap, ProviderSnapshot)
    assert snap.model == "openai/gpt-4.1"
    assert snap.context_window_tokens == cfg.agents.defaults.context_window_tokens
    assert snap.signature == provider_signature(cfg)
    assert snap.provider.__class__.__name__ == "OpenAICompatProvider"


def test_huggingface_routes_via_openai_compat_with_router_base_url():
    """HF Inference Providers ship as a gateway entry, default base = router URL."""
    cfg = _config("huggingface/Qwen/Qwen2.5-72B-Instruct", huggingface={"apiKey": "hf_test"})
    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI") as mock_client:
        make_provider(cfg)
    # Confirm the api_base passed to AsyncOpenAI is the HF router endpoint.
    base_url = mock_client.call_args.kwargs.get("base_url") or mock_client.call_args.kwargs.get("api_base")
    assert "huggingface" in (base_url or "")


def test_make_provider_threads_extra_body_into_openai_compat():
    """Per-provider extra_body in config is wired through to the provider."""
    cfg = _config("openai/gpt-4.1", openai={"apiKey": "sk-test"})
    cfg.providers.openai.extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = make_provider(cfg)
    assert provider._extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_openai_codex_provider_supports_progress_deltas():
    """Codex streaming UX depends on the class flag being set."""
    from pythinker.providers.openai_codex_provider import OpenAICodexProvider

    assert OpenAICodexProvider.supports_progress_deltas is True
