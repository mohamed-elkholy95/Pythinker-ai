from pythinker.config.schema import Config


def test_trace_match_provider_reports_keyword_phase() -> None:
    cfg = Config()
    cfg.providers.openai.api_key = "sk-test"

    trace = cfg._trace_match_provider("openai/gpt-4.6")

    assert trace["matched_spec"] == cfg.get_provider_name("openai/gpt-4.6")
    assert trace["matched_keyword"]
    assert trace["match_phase"] in {
        "forced-provider",
        "prefix-match",
        "keyword-match",
        "local-fallback",
        "api-key-fallback",
        "none",
    }
    assert trace["resolved_api_base"] == cfg.get_api_base("openai/gpt-4.6")


def test_trace_match_provider_does_not_change_hot_path_result() -> None:
    cfg = Config()
    cfg.providers.openrouter.api_key = "sk-or-test"

    trace = cfg._trace_match_provider("openrouter/auto")

    assert trace["matched_spec"] == cfg.get_provider_name("openrouter/auto")
