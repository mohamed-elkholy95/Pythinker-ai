from pythinker.providers.model_profiles import canonical_model_id


def test_canonical_model_id_strips_gateway_and_vendor_prefixes():
    assert canonical_model_id("openrouter/anthropic/claude-opus-4-7") == "claude-opus-4-7"


def test_canonical_model_id_keeps_unknown_prefix_context():
    assert canonical_model_id("unknown/openai/gpt-5.5") == "unknown/openai/gpt-5.5"
