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
    input_cost_per_million: float | None = None
    cached_input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    currency: str = "USD"
    pricing_source_url: str | None = None
    long_context_threshold_tokens: int | None = None
    long_context_input_multiplier: float | None = None
    long_context_output_multiplier: float | None = None
    is_alias: bool = False

PYTHINKER_PROVIDER_PREFIXES = frozenset({"openai", "openai_codex", "openai-codex", "azure_openai", "azure-openai", "anthropic", "github_copilot", "github-copilot", "gemini", "openrouter", "aihubmix", "litellm", "vercel_ai_gateway", "deepseek", "zhipu", "dashscope", "moonshot", "minimax", "minimax_anthropic", "mistral", "stepfun", "xiaomi_mimo", "vllm", "ollama", "lm_studio", "ovms", "groq", "qianfan", "xai", "cerebras", "together", "fireworks", "huggingface", "siliconflow", "volcengine", "byteplus", "custom"})
def _metadata_from_row(row: dict[str, Any], *, alias: bool = False) -> ModelMetadata:
    data = dict(row)
    data["aliases"] = tuple(data.get("aliases") or ())
    data["source"] = MetadataSource(data.get("source", MetadataSource.CURATED.value))
    data["is_alias"] = alias or bool(data.get("is_alias", False))
    return ModelMetadata(**data)
def _load_profiles() -> tuple[ModelMetadata, ...]:
    path = Path(__file__).with_name("model_profiles.json")
    return tuple(_metadata_from_row(r) for r in json.loads(path.read_text()).get("models", []))

_PROFILES = _load_profiles()
_CURATED = {(m.provider, m.model_id.lower()): m for m in _PROFILES}
_ALIASES = {(m.provider, a.lower()): _metadata_from_row(m.__dict__, alias=True) for m in _PROFILES for a in m.aliases}
def resolve_model_alias(model: str) -> str:
    meta = get_model_metadata(model)
    return meta.model_id if meta and meta.is_alias else model
def _candidate_keys(model: str) -> list[tuple[str | None, str]]:
    parts = model.split("/")
    candidates: list[tuple[str | None, str]] = []
    for i, prefix in enumerate(parts):
        rest = "/".join(parts[i + 1:])
        if not rest:
            candidates.append((None, prefix))
            continue
        normalized = prefix.replace("-", "_").lower()
        if normalized in PYTHINKER_PROVIDER_PREFIXES:
            candidates.append((normalized, rest))
        candidates.append((prefix.lower(), rest))
    candidates.append((None, parts[-1]))
    return candidates
def _override_metadata(model: str, override: Any) -> ModelMetadata:
    data = override.model_dump(exclude_none=True) if hasattr(override, "model_dump") else dict(override)
    data.setdefault("provider", model.split("/", 1)[0].replace("-", "_") if "/" in model else "custom")
    data.setdefault("model_id", model.split("/", 1)[-1])
    data.update(source=MetadataSource.USER_OVERRIDE, confidence="official")
    return _metadata_from_row(data)
def _lookup_curated(model: str) -> ModelMetadata | None:
    for provider, canonical in _candidate_keys(model):
        for candidate_provider in ([provider] if provider else [p for p, _ in _CURATED]):
            key = (candidate_provider, canonical.lower())
            if key in _CURATED:
                return _CURATED[key]
            if key in _ALIASES:
                return _ALIASES[key]
    return None
def get_model_metadata(model: str, *, config: Any | None = None) -> ModelMetadata | None:
    models = getattr(config, "models", None)
    overrides = getattr(models, "overrides", {}) if models is not None else {}
    if model in overrides:
        return _override_metadata(model, overrides[model])
    azure = getattr(models, "azure_deployments", {}) if models is not None else {}
    if "/" in model and model.split("/", 1)[0].replace("-", "_") == "azure_openai":
        deployment = model.split("/", 1)[1]
        if deployment in azure:
            base = azure[deployment]
            return _override_metadata(base, overrides[base]) if base in overrides else _lookup_curated(f"openai_codex/{base}") or _lookup_curated(f"openai/{base}")
    return _lookup_curated(model)
