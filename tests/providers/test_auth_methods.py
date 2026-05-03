"""Test ProviderSpec.auth_methods for OAuth and regional providers."""

from pythinker.providers.registry import PROVIDERS


def test_openai_codex_has_browser_login():
    spec = next(s for s in PROVIDERS if s.name == "openai_codex")
    method_ids = {m.id for m in spec.auth_methods}
    assert "browser-login" in method_ids


def test_github_copilot_has_browser_login():
    spec = next(s for s in PROVIDERS if s.name == "github_copilot")
    method_ids = {m.id for m in spec.auth_methods}
    assert "browser-login" in method_ids


def test_minimax_has_regional_methods():
    spec = next(s for s in PROVIDERS if s.name == "minimax")
    method_ids = {m.id for m in spec.auth_methods}
    # API key entries are required; OAuth entries are placeholders for future.
    assert "api-key-cn" in method_ids
    assert "api-key-global" in method_ids


def test_api_key_only_provider_has_empty_auth_methods():
    # DeepSeek is a pure API-key provider.
    spec = next(s for s in PROVIDERS if s.name == "deepseek")
    assert spec.auth_methods == []
