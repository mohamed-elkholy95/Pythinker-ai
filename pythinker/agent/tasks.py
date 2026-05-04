from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

TaskType = Literal["subagent", "shell", "remote_agent", "dream", "workflow"]
TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled", "orphaned"]

_TASK_ID_PREFIXES: dict[str, str] = {
    "subagent": "a_",
    "shell": "s_",
    "remote_agent": "r_",
    "dream": "d_",
    "workflow": "w_",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def generate_task_id(task_type: TaskType | str) -> str:
    prefix = _TASK_ID_PREFIXES.get(task_type)
    if prefix is None:
        raise ValueError(f"unknown task type: {task_type}")
    return f"{prefix}{uuid4().hex[:8]}"


@dataclass
class TaskOutputRef:
    task_id: str
    content: str = ""
    offset: int = 0
    truncated: bool = False
    error: str | None = None


@dataclass
class TaskRecord:
    task_id: str
    type: TaskType | str
    label: str
    description: str
    session_key: str
    status: TaskStatus | str
    started_at: str
    updated_at: str
    ended_at: str | None = None
    tool_use_id: str | None = None
    output_uri: str | None = None
    output_offset: int = 0
    recent_activity: list[dict[str, str]] | None = None
    usage: dict[str, object] | None = None
    stop_reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
