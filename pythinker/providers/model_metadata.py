"""Static model metadata registry for context-window budgeting."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

class MetadataSource(StrEnum):
    OFFICIAL_DOCS = "official_docs"
    PROVIDER_API = "provider_api"
    CURATED = "curated"
    USER_OVERRIDE = "user_override"
    FALLBACK = "fallback"

@dataclass(frozen=True)
class ModelMetadata:
    provider: str
    model_id: str
    aliases: tuple[str, ...] = ()
    input_tokens: int | None = None
    max_output_tokens: int | None = None
    total_context_tokens: int | None = None
    encoding: str = "cl100k_base"
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_json_schema: bool | None = None
    supports_reasoning: bool | None = None
    preferred_api: Literal["chat_completions", "responses", "anthropic_messages"] | None = None
    count_tokens_supported: bool = False
    runtime_metadata_supported: bool = False
    source: MetadataSource = MetadataSource.CURATED
    source_url: str | None = None
    fetched_at: str | None = None
    confidence: Literal["official", "provider_api", "curated", "fallback"] = "curated"
    is_alias: bool = False

_PROVIDER_PREFIXES = {
    "anthropic", "azure_openai", "gemini", "github_copilot", "openai", "openai_codex",
}


def _metadata_from_row(row: dict[str, Any], *, alias: bool = False) -> ModelMetadata:
    data = dict(row)
    data["aliases"] = tuple(data.get("aliases") or ())
    data["source"] = MetadataSource(data.get("source", MetadataSource.CURATED.value))
    data["is_alias"] = alias or bool(data.get("is_alias", False))
    return ModelMetadata(**data)


def _load_profiles() -> tuple[ModelMetadata, ...]:
    path = Path(__file__).with_name("model_profiles.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(_metadata_from_row(row) for row in raw.get("models", []))

_PROFILES = _load_profiles()
_CURATED = {(m.provider, m.model_id.lower()): m for m in _PROFILES}
_ALIASES = {
    (m.provider, alias.lower()): _metadata_from_row(m.__dict__, alias=True)
    for m in _PROFILES
    for alias in m.aliases
}


def resolve_model_alias(model: str) -> str:
    """Return the canonical model id for a known alias, otherwise the input."""
    meta = get_model_metadata(model)
    return meta.model_id if meta and meta.is_alias else model


def _candidate_keys(model: str) -> list[tuple[str | None, str]]:
    parts = model.split("/", 1)
    if len(parts) == 1:
        return [(None, model)]
    provider = parts[0].replace("-", "_").lower()
    rest = parts[1]
    keys: list[tuple[str | None, str]] = []
    if provider in _PROVIDER_PREFIXES:
        keys.append((provider, rest))
    keys.extend([(parts[0].lower(), rest), (None, rest)])
    return keys


def get_model_metadata(model: str, *, config: Any | None = None) -> ModelMetadata | None:
    """Look up static metadata for a provider-qualified or bare model id."""
    del config  # Config override support is added in the next task.
    for provider, canonical in _candidate_keys(model):
        lowered = canonical.lower()
        provider_keys = [provider] if provider else [p for p, _ in _CURATED]
        for candidate_provider in provider_keys:
            key = (candidate_provider, lowered)
            if key in _CURATED:
                return _CURATED[key]
            if key in _ALIASES:
                return _ALIASES[key]
    return None
