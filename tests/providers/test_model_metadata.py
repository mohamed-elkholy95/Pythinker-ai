from pythinker.providers.model_metadata import (
    MetadataSource,
    get_model_metadata,
)


def test_openai_codex_prefix_resolves_to_curated_gpt_profile():
    meta = get_model_metadata("openai-codex/gpt-5.5")
    assert meta is not None
    assert meta.provider in {"openai", "openai_codex"}
    assert meta.input_tokens == 272_000
    assert meta.max_output_tokens == 128_000
    assert meta.total_context_tokens == 400_000
    assert meta.encoding == "o200k_base"
    assert meta.source == MetadataSource.CURATED
    assert meta.input_cost_per_million == 5.0
    assert meta.cached_input_cost_per_million == 0.5
    assert meta.output_cost_per_million == 30.0
    assert meta.currency == "USD"


def test_chat_preview_keeps_smaller_usable_input_budget():
    meta = get_model_metadata("openai/gpt-5.2-chat")
    assert meta is not None
    assert meta.total_context_tokens == 128_000
    assert meta.input_tokens == 111_616
    assert meta.max_output_tokens == 16_384


def test_unknown_model_returns_none_not_fake_limit():
    assert get_model_metadata("custom/my-unlisted-model") is None


def test_latest_alias_is_marked_lower_confidence_if_unresolved():
    meta = get_model_metadata("gemini/gemini-flash-latest")
    assert meta is not None
    assert meta.is_alias is True
    assert meta.confidence in {"provider_api", "curated", "fallback"}
