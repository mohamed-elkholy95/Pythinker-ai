from scripts.update_model_metadata import (
    parse_anthropic_models,
    parse_gemini_models,
    parse_openrouter_models,
    sorted_profile_payload,
)


def test_parse_anthropic_models_shape():
    rows = parse_anthropic_models(
        {"data": [{"id": "claude-x", "max_input_tokens": 200000, "max_tokens": 32000}]}
    )
    assert rows[0]["provider"] == "anthropic"
    assert rows[0]["model_id"] == "claude-x"
    assert rows[0]["input_tokens"] == 200000
    assert rows[0]["max_output_tokens"] == 32000
    assert rows[0]["count_tokens_supported"] is True


def test_parse_openrouter_models_shape():
    rows = parse_openrouter_models(
        {"data": [{"id": "anthropic/claude-x", "context_length": 1000,
                   "top_provider": {"max_completion_tokens": 200}}]}
    )
    assert rows[0]["provider"] == "openrouter"
    assert rows[0]["input_tokens"] == 800
    assert rows[0]["max_output_tokens"] == 200


def test_parse_gemini_latest_alias():
    rows = parse_gemini_models(
        {"models": [{"name": "models/gemini-3-flash", "inputTokenLimit": 100,
                     "outputTokenLimit": 10, "aliases": ["gemini-flash-latest"]}]}
    )
    assert rows[0]["model_id"] == "gemini-3-flash"
    assert rows[0]["aliases"] == ["gemini-flash-latest"]
    assert rows[0]["confidence"] == "provider_api"


def test_sorted_profile_payload_is_deterministic():
    payload = sorted_profile_payload([
        {"provider": "z", "model_id": "b"},
        {"provider": "a", "model_id": "c"},
    ])
    assert [row["provider"] for row in payload["models"]] == ["a", "z"]
