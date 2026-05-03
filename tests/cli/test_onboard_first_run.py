"""First-run LLM-provider nudge in run_onboard."""

from pythinker.cli.onboard import _no_provider_key_set
from pythinker.config.schema import Config


def test_no_provider_key_set_true_on_default_config():
    """A pristine Config has no provider keys → predicate returns True."""
    assert _no_provider_key_set(Config()) is True


def test_no_provider_key_set_false_when_direct_provider_configured():
    cfg = Config()
    cfg.providers.openai.api_key = "sk-test"
    assert _no_provider_key_set(cfg) is False


def test_no_provider_key_set_false_when_only_gateway_configured():
    """Regression: gateways (OpenRouter etc.) count as 'configured' even
    though signup_url_required(spec) excludes them — the nudge predicate
    must NOT use signup_url_required."""
    cfg = Config()
    cfg.providers.openrouter.api_key = "sk-or-v1-test"
    assert _no_provider_key_set(cfg) is False


def test_no_provider_key_set_false_when_only_local_backend_configured():
    """Local backends (Ollama, vLLM) are also valid first-provider choices."""
    cfg = Config()
    cfg.providers.ollama.api_key = "ollama-token"
    assert _no_provider_key_set(cfg) is False



