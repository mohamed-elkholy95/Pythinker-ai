"""Compatibility model profiles derived from metadata rows."""
from __future__ import annotations

import re
from dataclasses import dataclass

from pythinker.providers.model_metadata import (
    PYTHINKER_PROVIDER_PREFIXES,
    ModelMetadata,
    get_model_metadata,
)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    canonical_id: str
    context: int
    input: int
    output: int
    encoding: str


_GPT_VERSION_RE = re.compile(r"^gpt-(\d+\.\d+)")
_GPT_5_5_OPENAI = ModelProfile("gpt-5.5", 1_050_000, 1_050_000, 128_000, "o200k_base")
_GPT_5_5_CODEX = ModelProfile("gpt-5.5", 400_000, 272_000, 128_000, "o200k_base")
_GPT_5_4_OPENAI = ModelProfile("gpt-5.4", 1_050_000, 1_050_000, 128_000, "o200k_base")
_CLAUDE_OPUS_4_7 = ModelProfile("claude-opus-4-7", 1_000_000, 900_000, 64_000, "cl100k_base")
_CLAUDE_SONNET_4_6 = ModelProfile("claude-sonnet-4-6", 200_000, 180_000, 8_192, "cl100k_base")
_STATIC = {
    ("anthropic", "claude-opus-4-7"): _CLAUDE_OPUS_4_7,
    ("anthropic", "claude-sonnet-4-6"): _CLAUDE_SONNET_4_6,
    (None, "claude-opus-4-7"): _CLAUDE_OPUS_4_7,
    (None, "claude-sonnet-4-6"): _CLAUDE_SONNET_4_6,
}


def _extract_provider(model: str) -> tuple[str | None, str]:
    parts = model.split("/")
    provider: str | None = None
    while parts and parts[0].replace("-", "_").lower() in PYTHINKER_PROVIDER_PREFIXES:
        provider = parts.pop(0).replace("-", "_").lower()
    canonical = "/".join(parts) if parts else model
    return provider, canonical.lower()


def canonical_model_id(model: str) -> str:
    """Return the bare model id with known provider prefixes stripped."""
    return _extract_provider(model)[1]


def _from_metadata(meta: ModelMetadata) -> ModelProfile | None:
    if not (meta.input_tokens and meta.max_output_tokens):
        return None
    return ModelProfile(
        canonical_id=meta.model_id,
        context=meta.total_context_tokens or meta.input_tokens,
        input=meta.input_tokens,
        output=meta.max_output_tokens,
        encoding=meta.encoding,
    )


def _family_fallback(provider: str | None, canonical: str) -> ModelProfile | None:
    match = _GPT_VERSION_RE.match(canonical)
    if not match:
        return None
    try:
        minor = float(match.group(1))
    except ValueError:
        return None
    if provider == "openai_codex" and minor >= 5.1:
        return _GPT_5_5_CODEX
    if provider != "openai_codex" and minor >= 5.5:
        return _GPT_5_5_OPENAI
    if provider != "openai_codex" and minor >= 5.4:
        return _GPT_5_4_OPENAI
    return None


def get_profile(model: str) -> ModelProfile | None:
    """Return model caps and local tokenizer hint for ``model``."""
    provider, canonical = _extract_provider(model)
    static = _STATIC.get((provider, canonical)) or _STATIC.get((None, canonical))
    if static:
        return static
    meta = get_model_metadata(model)
    profile = _from_metadata(meta) if meta is not None else None
    return profile or _family_fallback(provider, canonical)
