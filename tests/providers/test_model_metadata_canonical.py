from pythinker.config.schema import Config, ModelMetadataOverride, ModelsConfig
from pythinker.providers.model_metadata import get_model_metadata
from pythinker.providers.model_profiles import canonical_model_id


def test_two_segment_gateway_id_resolves():
    meta = get_model_metadata("openrouter/anthropic/claude-opus-4-7")
    assert meta is not None
    assert meta.model_id == "claude-opus-4-7"
    assert meta.provider in {"anthropic", "openrouter"}


def test_strip_first_segment_when_first_is_pythinker_provider():
    meta = get_model_metadata("openai-codex/gpt-5.5")
    assert meta is not None
    assert meta.provider == "openai_codex"
    assert meta.input_tokens == 272_000


def test_user_override_keyed_same_as_curated():
    cfg = Config(models=ModelsConfig(overrides={
        "openai-codex/gpt-5.5": ModelMetadataOverride(input_tokens=200_000),
    }))
    meta = get_model_metadata("openai-codex/gpt-5.5", config=cfg)
    assert meta is not None
    assert meta.input_tokens == 200_000


def test_progressive_suffix_falls_back_to_pinned():
    meta = get_model_metadata("unknown-gateway/openai/gpt-5.5")
    assert meta is not None
    assert meta.model_id == "gpt-5.5"


def test_no_match_returns_none():
    assert get_model_metadata("custom/unlisted/v0") is None


def test_canonical_model_id_strips_known_provider_prefixes():
    assert canonical_model_id("openrouter/anthropic/claude-opus-4-7") == "claude-opus-4-7"
