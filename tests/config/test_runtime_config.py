"""Tests for the new Config.runtime block."""

from pythinker.config.schema import Config


def test_runtime_defaults_to_off():
    cfg = Config()
    assert cfg.runtime.policy_enabled is False
    assert cfg.runtime.policy_migration_mode is None  # deny-default when policy is enabled
    assert cfg.runtime.telemetry_sink == "off"
    assert cfg.runtime.telemetry_jsonl_path is None
    assert cfg.runtime.session_cache_max == 256
    assert cfg.runtime.max_tool_calls_per_turn == 0
    assert cfg.runtime.max_wall_clock_s == 0.0
    assert cfg.runtime.max_subagent_recursion_depth == 3
    assert cfg.runtime.manifests_dir is None
    assert cfg.runtime.default_agent_id == "default"


def test_runtime_accepts_camelcase_aliases():
    cfg = Config.model_validate(
        {"runtime": {
            "policyEnabled": True,
            "policyMigrationMode": "allow-all",
            "telemetrySink": "jsonl",
            "telemetryJsonlPath": "/tmp/events.jsonl",
            "sessionCacheMax": 1024,
            "maxToolCallsPerTurn": 50,
            "maxWallClockS": 120.0,
            "maxSubagentRecursionDepth": 5,
            "manifestsDir": "/tmp/agents",
            "defaultAgentId": "research",
        }}
    )
    assert cfg.runtime.policy_enabled is True
    assert cfg.runtime.policy_migration_mode == "allow-all"
    assert cfg.runtime.telemetry_sink == "jsonl"
    assert cfg.runtime.telemetry_jsonl_path == "/tmp/events.jsonl"
    assert cfg.runtime.session_cache_max == 1024
    assert cfg.runtime.max_tool_calls_per_turn == 50
    assert cfg.runtime.max_wall_clock_s == 120.0
    assert cfg.runtime.max_subagent_recursion_depth == 5
    assert cfg.runtime.manifests_dir == "/tmp/agents"
    assert cfg.runtime.default_agent_id == "research"


def test_runtime_rejects_unknown_migration_mode():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Config.model_validate({"runtime": {"policyMigrationMode": "yolo"}})
