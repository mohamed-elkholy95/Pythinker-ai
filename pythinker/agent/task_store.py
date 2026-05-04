from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pythinker.agent.tasks import (
    TaskOutputRef,
    TaskRecord,
    TaskStatus,
    TaskType,
    generate_task_id,
    utc_now_iso,
)

_ACTIVE_STATUSES = {"pending", "running"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "orphaned"}
_MAX_RECENT_ACTIVITY = 10


class TaskStore:
    """In-process task registry with append-only output files under the workspace.

    Single-user, local-only. On restart the registry starts empty; surviving
    output files are loaded as orphans so `/task-output <id>` keeps working.
    """

    def __init__(self, workspace: Path | str, max_recent: int = 200) -> None:
        self.workspace = Path(workspace)
        self.max_recent = max_recent
        self.output_dir = self.workspace / ".pythinker" / "task-results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, TaskRecord] = {}
        self._session_index: dict[str, set[str]] = {}
        self._load_orphaned_outputs()

    def start_task(
        self,
        *,
        task_type: TaskType | str,
        label: str,
        description: str,
        session_key: str,
        tool_use_id: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        now = utc_now_iso()
        task_id = task_id or generate_task_id(task_type)
        if not _is_safe_task_id(task_id):
            raise ValueError("invalid task id")
        record = self._records.get(task_id)
        if record is None:
            record = TaskRecord(
                task_id=task_id,
                type=task_type,
                label=label,
                description=description,
                session_key=session_key,
                status="running",
                started_at=now,
                updated_at=now,
                tool_use_id=tool_use_id,
                recent_activity=[],
            )
        else:
            self._unindex_session(record)
            record.type = task_type
            record.label = label
            record.description = description
            record.session_key = session_key
            record.status = "running"
            record.updated_at = now
            record.ended_at = None
            record.tool_use_id = tool_use_id
            record.stop_reason = None
            record.error = None
            record.recent_activity = record.recent_activity or []
        self._records[task_id] = record
        self._index_session(record)
        self._trim_recent()
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def update_task(
        self,
        task_id: str,
        *,
        recent_activity: dict[str, str] | list[dict[str, str]] | None = None,
        usage: dict[str, object] | None = None,
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is None:
            return None
        record.updated_at = utc_now_iso()
        if recent_activity is not None:
            if record.recent_activity is None:
                record.recent_activity = []
            if isinstance(recent_activity, dict):
                record.recent_activity.append(recent_activity)
            else:
                record.recent_activity.extend(recent_activity)
            if len(record.recent_activity) > _MAX_RECENT_ACTIVITY:
                record.recent_activity = record.recent_activity[-_MAX_RECENT_ACTIVITY:]
        if usage is not None:
            record.usage = usage
        if stop_reason is not None:
            record.stop_reason = stop_reason
        if error is not None:
            record.error = error
        self._trim_recent()
        return record

    def finish_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | str = "completed",
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is None:
            return None
        now = utc_now_iso()
        record.status = status
        record.updated_at = now
        record.ended_at = now
        if stop_reason is not None:
            record.stop_reason = stop_reason
        if error is not None:
            record.error = error
        self._trim_recent()
        return record

    def cancel_task(self, task_id: str) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is not None and record.status in _TERMINAL_STATUSES:
            return record
        return self.finish_task(task_id, status="cancelled")

    def append_output(self, task_id: str, content: str) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is None:
            return None
        path = self._output_path(task_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        record.output_uri = self._output_uri(task_id)
        record.output_offset = path.stat().st_size
        record.updated_at = utc_now_iso()
        return record

    def read_output(self, task_id: str, max_chars: int = 16000) -> TaskOutputRef:
        if not _is_safe_task_id(task_id):
            return TaskOutputRef(task_id=task_id, error="task output not found")
        path = self._output_path(task_id)
        if not path.is_file():
            return TaskOutputRef(task_id=task_id, error="task output not found")

        size = path.stat().st_size
        if max_chars <= 0:
            return TaskOutputRef(
                task_id=task_id, content="", offset=size, truncated=size > 0
            )
        max_bytes = max_chars * 4
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read(max_bytes)
        content = data.decode("utf-8", errors="ignore")
        truncated = size > len(data) or len(content) > max_chars
        if len(content) > max_chars:
            content = content[-max_chars:]
        return TaskOutputRef(
            task_id=task_id, content=content, offset=size, truncated=truncated
        )

    def list_records(
        self,
        *,
        session_key: str | None = None,
        include_terminal: bool = True,
    ) -> list[TaskRecord]:
        if session_key is not None:
            records = [
                self._records[tid]
                for tid in self._session_index.get(session_key, set())
                if tid in self._records
            ]
        else:
            records = list(self._records.values())
        if not include_terminal:
            records = [r for r in records if r.status in _ACTIVE_STATUSES]
        return sorted(records, key=lambda r: r.updated_at, reverse=True)

    def _load_orphaned_outputs(self) -> None:
        for path in sorted(self.output_dir.glob("*.txt")):
            task_id = path.stem
            if not _is_safe_task_id(task_id) or not path.is_file():
                continue
            stat = path.stat()
            ts = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
            self._records[task_id] = TaskRecord(
                task_id=task_id,
                type="subagent",
                label="",
                description="",
                session_key="",
                status="orphaned",
                started_at=ts,
                updated_at=ts,
                ended_at=ts,
                output_uri=self._output_uri(task_id),
                output_offset=stat.st_size,
                recent_activity=[],
            )
        self._trim_recent()

    def _trim_recent(self) -> None:
        if len(self._records) <= self.max_recent:
            return
        for record in self.list_records()[self.max_recent :]:
            if record.status in _TERMINAL_STATUSES:
                self._records.pop(record.task_id, None)
                self._unindex_session(record)

    def _index_session(self, record: TaskRecord) -> None:
        if record.session_key:
            self._session_index.setdefault(record.session_key, set()).add(record.task_id)

    def _unindex_session(self, record: TaskRecord) -> None:
        ids = self._session_index.get(record.session_key)
        if ids is None:
            return
        ids.discard(record.task_id)
        if not ids:
            self._session_index.pop(record.session_key, None)

    def _output_path(self, task_id: str) -> Path:
        return self.output_dir / f"{task_id}.txt"

    def _output_uri(self, task_id: str) -> str:
        return f"task-output://{task_id}"


def _is_safe_task_id(task_id: str) -> bool:
    return bool(task_id) and Path(task_id).name == task_id and "\\" not in task_id
