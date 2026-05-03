"""Workspace-scoped provider token usage ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ledger_path(workspace: Path) -> Path:
    return workspace / "admin" / "usage.jsonl"


def _usage_int(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0


def record_turn_usage(
    *,
    workspace: Path,
    session_key: str | None,
    provider: str,
    model: str,
    usage: dict[str, Any],
) -> None:
    if not usage:
        return
    prompt = _usage_int(usage, "prompt_tokens")
    completion = _usage_int(usage, "completion_tokens")
    total = _usage_int(usage, "total_tokens") or prompt + completion
    if total <= 0 and prompt <= 0 and completion <= 0:
        return
    path = _ledger_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_key": session_key,
        "provider": provider,
        "model": model,
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cached_tokens": _usage_int(usage, "cached_tokens"),
        },
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_usage_summary(workspace: Path, *, recent_limit: int = 20) -> dict[str, Any]:
    path = _ledger_path(workspace)
    summary: dict[str, Any] = {
        "turns": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "by_model": {},
        "recent": [],
    }
    if not path.exists():
        return summary

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = row.get("usage")
            if not isinstance(usage, dict):
                continue
            model = str(row.get("model") or "unknown")
            prompt = _usage_int(usage, "prompt_tokens")
            completion = _usage_int(usage, "completion_tokens")
            cached = _usage_int(usage, "cached_tokens")
            total = _usage_int(usage, "total_tokens") or prompt + completion
            summary["turns"] += 1
            summary["prompt_tokens"] += prompt
            summary["completion_tokens"] += completion
            summary["cached_tokens"] += cached
            summary["total_tokens"] += total
            by_model = summary["by_model"].setdefault(
                model,
                {
                    "turns": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                    "total_tokens": 0,
                },
            )
            by_model["turns"] += 1
            by_model["prompt_tokens"] += prompt
            by_model["completion_tokens"] += completion
            by_model["cached_tokens"] += cached
            by_model["total_tokens"] += total
            rows.append({
                "timestamp": row.get("timestamp"),
                "session_key": row.get("session_key"),
                "provider": row.get("provider"),
                "model": model,
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cached_tokens": cached,
                    "total_tokens": total,
                },
            })
    summary["recent"] = rows[-recent_limit:][::-1]
    return summary
