from pathlib import Path

from pythinker.agent.usage_ledger import (
    load_usage_summary,
    record_turn_usage,
)


def test_usage_ledger_records_and_summarizes_turn_usage(tmp_path: Path) -> None:
    record_turn_usage(
        workspace=tmp_path,
        session_key="websocket:abc",
        provider="openai",
        model="openai/gpt-4o",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    record_turn_usage(
        workspace=tmp_path,
        session_key="slack:C123",
        provider="anthropic",
        model="anthropic/claude",
        usage={"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
    )

    summary = load_usage_summary(tmp_path)

    assert summary["total_tokens"] == 42
    assert summary["prompt_tokens"] == 30
    assert summary["completion_tokens"] == 12
    assert summary["turns"] == 2
    assert summary["by_model"]["openai/gpt-4o"]["total_tokens"] == 15
    assert summary["recent"][0]["session_key"] == "slack:C123"


def test_usage_ledger_estimates_cost_for_priced_models(tmp_path: Path) -> None:
    record_turn_usage(
        workspace=tmp_path,
        session_key="websocket:abc",
        provider="openai_codex",
        model="openai-codex/gpt-5.5",
        usage={
            "prompt_tokens": 1_000_000,
            "completion_tokens": 100_000,
            "total_tokens": 1_100_000,
            "cached_tokens": 200_000,
        },
    )

    summary = load_usage_summary(tmp_path)

    # (800k uncached * $5 + 200k cached * $0.50 + 100k output * $30) / 1M
    assert summary["cost"] == 7.1
    assert summary["currency"] == "USD"
    assert summary["priced_turns"] == 1
    assert summary["unpriced_turns"] == 0
    assert summary["by_model"]["openai-codex/gpt-5.5"]["cost"] == 7.1
    assert summary["recent"][0]["cost"] == 7.1


def test_usage_ledger_keeps_cost_null_when_no_pricing_is_known(tmp_path: Path) -> None:
    record_turn_usage(
        workspace=tmp_path,
        session_key="websocket:abc",
        provider="custom",
        model="custom/unpriced-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )

    summary = load_usage_summary(tmp_path)

    assert summary["cost"] is None
    assert summary["currency"] is None
    assert summary["priced_turns"] == 0
    assert summary["unpriced_turns"] == 1


def test_usage_ledger_ignores_empty_usage(tmp_path: Path) -> None:
    record_turn_usage(
        workspace=tmp_path,
        session_key="websocket:abc",
        provider="openai",
        model="openai/gpt-4o",
        usage={},
    )

    summary = load_usage_summary(tmp_path)

    assert summary["turns"] == 0
    assert summary["total_tokens"] == 0
