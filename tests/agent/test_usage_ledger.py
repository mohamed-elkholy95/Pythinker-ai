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
