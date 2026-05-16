from pythinker.config.schema import Config
from pythinker.providers.model_metadata import MetadataSource, get_model_metadata


def test_models_config_accepts_camelcase_aliases():
    cfg = Config.model_validate(
        {"models": {"metadataMode": "static", "metadataCacheTtlHours": 24}}
    )
    assert cfg.models.metadata_mode == "static"
    assert cfg.models.metadata_cache_ttl_hours == 24


def test_user_override_wins_over_curated_metadata():
    cfg = Config(
        models={
            "overrides": {
                "openai-codex/gpt-5.5": {
                    "provider": "openai_codex",
                    "inputTokens": 1234,
                    "maxOutputTokens": 567,
                    "totalContextTokens": 1801,
                    "encoding": "o200k_base",
                }
            }
        }
    )
    meta = get_model_metadata("openai-codex/gpt-5.5", config=cfg)
    assert meta is not None
    assert meta.source == MetadataSource.USER_OVERRIDE
    assert meta.input_tokens == 1234
    assert meta.max_output_tokens == 567
    assert meta.total_context_tokens == 1801


def test_azure_deployment_maps_to_base_model_metadata():
    cfg = Config(models={"azureDeployments": {"prod-agent": "gpt-5.2-codex"}})
    meta = get_model_metadata("azure_openai/prod-agent", config=cfg)
    assert meta is not None
    assert meta.model_id == "gpt-5.2-codex"
    assert meta.input_tokens == 272_000
