from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
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
    return f"{prefix}{uuid4().hex}"


@dataclass
class TaskOutputRef:
    task_id: str
    content: str = ""
    offset: int = 0
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"task_id": self.task_id}
        if self.content:
            payload["content"] = self.content
        if self.offset:
            payload["offset"] = self.offset
        if self.truncated:
            payload["truncated"] = self.truncated
        if self.error:
            payload["error"] = self.error
        return payload


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
        payload: dict[str, object] = {
            "task_id": self.task_id,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "session_key": self.session_key,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "tool_use_id": self.tool_use_id,
            "output_uri": self.output_uri,
            "output_offset": self.output_offset,
            "recent_activity": self.recent_activity or [],
            "usage": _json_safe(self.usage),
            "stop_reason": self.stop_reason,
            "error": self.error,
        }
        return payload


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)
