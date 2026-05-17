"""Compatibility helpers for model profile identifiers."""

from pythinker.providers.model_metadata import PYTHINKER_PROVIDER_PREFIXES


def canonical_model_id(model: str) -> str:
    """Strip known Pythinker/gateway provider prefixes from a model id."""
    parts = model.split("/")
    while parts and parts[0].replace("-", "_").lower() in PYTHINKER_PROVIDER_PREFIXES:
        parts.pop(0)
    return "/".join(parts) if parts else model
