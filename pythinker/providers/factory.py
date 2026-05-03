"""Create LLM providers from config."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pythinker.config.schema import Config
from pythinker.providers.base import GenerationSettings, LLMProvider
from pythinker.providers.registry import find_by_name


@dataclass(frozen=True)
class ProviderSnapshot:
    """A constructed provider plus the inputs it was built from.

    `signature` is the tuple of config fields that affect provider identity;
    callers can compare two snapshots' signatures to decide whether a hot
    reload is needed.
    """

    provider: LLMProvider
    model: str
    context_window_tokens: int
    signature: tuple[object, ...]


def make_provider(config: Config) -> LLMProvider:
    """Create the LLM provider implied by config.

    Routing is driven by `ProviderSpec.backend` in the registry. Errors during
    validation (missing key, missing Azure base) raise `ValueError` so callers
    can decide how to surface them — the CLI translates these into
    `console.print` + `typer.Exit(1)`; the SDK lets them propagate.
    """
    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    spec = find_by_name(provider_name) if provider_name else None
    backend = spec.backend if spec else "openai_compat"

    # --- validation ---
    if backend == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            raise ValueError("Azure OpenAI requires api_key and api_base in config.")
    elif backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            raise ValueError(f"No API key configured for provider '{provider_name}'.")

    # --- instantiation by backend ---
    if backend == "openai_codex":
        from pythinker.providers.openai_codex_provider import OpenAICodexProvider

        provider: LLMProvider = OpenAICodexProvider(default_model=model)
    elif backend == "azure_openai":
        from pythinker.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )
    elif backend == "github_copilot":
        from pythinker.providers.github_copilot_provider import GitHubCopilotProvider

        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "anthropic":
        from pythinker.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from pythinker.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
            extra_body=p.extra_body if p else None,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider


def provider_signature(config: Config) -> tuple[object, ...]:
    """Return the config fields that determine provider identity.

    Compare two signatures to detect whether `make_provider` would produce a
    different provider — useful for hot-reload paths that want to skip
    rebuilding when nothing material changed.
    """
    model = config.agents.defaults.model
    defaults = config.agents.defaults
    p = config.get_provider(model)
    extra_body_sig = (
        json.dumps(p.extra_body, sort_keys=True) if p and p.extra_body else None
    )
    extra_headers_sig = (
        json.dumps(sorted(p.extra_headers.items())) if p and p.extra_headers else None
    )
    return (
        model,
        defaults.provider,
        config.get_provider_name(model),
        config.get_api_key(model),
        config.get_api_base(model),
        defaults.max_tokens,
        defaults.temperature,
        defaults.reasoning_effort,
        defaults.context_window_tokens,
        extra_body_sig,
        extra_headers_sig,
    )


def build_provider_snapshot(config: Config) -> ProviderSnapshot:
    """Build a snapshot capturing both the provider and the inputs that made it."""
    return ProviderSnapshot(
        provider=make_provider(config),
        model=config.agents.defaults.model,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        signature=provider_signature(config),
    )


def load_provider_snapshot(config_path: Path | None = None) -> ProviderSnapshot:
    """Convenience: load+resolve config from disk and build a snapshot."""
    from pythinker.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot(resolve_config_env_vars(load_config(config_path)))
