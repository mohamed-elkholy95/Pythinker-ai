from __future__ import annotations

import errno
import json
import os
import stat
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
    def __init__(self, workspace: Path | str, max_recent: int = 200) -> None:
        self.workspace = Path(workspace)
        self.max_recent = max_recent
        self.output_dir = self.workspace / ".pythinker" / "task-results"
        self._ensure_output_dir_safe(create=True)
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
        if not self._is_safe_task_id(task_id):
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
            if record.recent_activity is None:
                record.recent_activity = []
        self._records[task_id] = record
        self._index_session(record)
        self._write_metadata(record)
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
        self._write_metadata(record)
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
        self._write_metadata(record)
        self._trim_recent()
        return record

    def cancel_task(self, task_id: str) -> TaskRecord | None:
        return self.finish_task(task_id, status="cancelled")

    def append_output(self, task_id: str, content: str) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is None:
            return None
        path = self._output_path(task_id)
        try:
            fd = self._open_no_symlink(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        except OSError as e:
            if e.errno == errno.ELOOP:
                return record
            raise
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            output_size = os.fstat(f.fileno()).st_size
        record.output_uri = self._output_uri(task_id)
        record.output_offset = output_size
        record.updated_at = utc_now_iso()
        self._write_metadata(record)
        return record

    def read_output(self, task_id: str, max_chars: int = 16000) -> TaskOutputRef:
        if not self._is_safe_task_id(task_id):
            return TaskOutputRef(task_id=task_id, error="task output not found")
        path = self._output_path(task_id)
        try:
            fd = self._open_no_symlink(path, os.O_RDONLY)
        except OSError as e:
            if e.errno in {errno.ENOENT, errno.ELOOP, errno.EISDIR}:
                return TaskOutputRef(task_id=task_id, error="task output not found")
            raise

        output_size = os.fstat(fd).st_size
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return TaskOutputRef(task_id=task_id, error="task output not found")

        if max_chars <= 0:
            os.close(fd)
            content = ""
            truncated = output_size > 0
        else:
            max_bytes = max_chars * 4
            with os.fdopen(fd, "rb") as f:
                if output_size > max_bytes:
                    f.seek(output_size - max_bytes)
                data = f.read(max_bytes)
            content = data.decode("utf-8", errors="ignore")
            truncated = output_size > len(data) or len(content) > max_chars
            if len(content) > max_chars:
                content = content[-max_chars:]
        return TaskOutputRef(
            task_id=task_id,
            content=content,
            offset=output_size,
            truncated=truncated,
        )

    def list_records(
        self,
        *,
        session_key: str | None = None,
        include_terminal: bool = True,
    ) -> list[TaskRecord]:
        if session_key is not None:
            records = [
                self._records[task_id]
                for task_id in self._session_index.get(session_key, set())
                if task_id in self._records
            ]
        else:
            records = list(self._records.values())
        if not include_terminal:
            records = [record for record in records if record.status in _ACTIVE_STATUSES]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def _load_orphaned_outputs(self) -> None:
        try:
            self._ensure_output_dir_safe(create=False)
        except OSError as e:
            if e.errno == errno.ELOOP:
                return
            raise
        loaded_task_ids: set[str] = set()
        for path in sorted(self.output_dir.glob("*.txt")):
            task_id = path.stem
            if not self._is_safe_task_id(task_id):
                continue
            output_size = self._safe_file_size(path)
            if output_size is None:
                continue
            record = self._load_metadata_record(task_id, path)
            if record is not None:
                self._records[task_id] = record
                self._index_session(record)
                loaded_task_ids.add(task_id)
                continue
            now = utc_now_iso()
            self._records[task_id] = TaskRecord(
                task_id=task_id,
                type="subagent",
                label="",
                description="",
                session_key="",
                status="orphaned",
                started_at=now,
                updated_at=now,
                ended_at=now,
                output_uri=self._output_uri(task_id),
                output_offset=output_size,
                recent_activity=[],
            )
            loaded_task_ids.add(task_id)
        for path in sorted(self.output_dir.glob("*.json")):
            task_id = path.stem
            if task_id in loaded_task_ids or not self._is_safe_task_id(task_id):
                continue
            record = self._load_metadata_record(task_id, None)
            if record is None:
                continue
            self._records[task_id] = record
            self._index_session(record)
        self._trim_recent()

    def _load_metadata_record(self, task_id: str, output_path: Path | None) -> TaskRecord | None:
        metadata_path = self._metadata_path(task_id)
        try:
            payload = json.loads(self._read_text_no_symlink(metadata_path))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("task_id") != task_id:
            return None

        now = utc_now_iso()
        status = self._metadata_str(payload.get("status"), "orphaned")
        ended_at = self._metadata_optional_str(payload.get("ended_at"))
        if status in _ACTIVE_STATUSES:
            status = "orphaned"
            ended_at = ended_at or now
        elif status not in _TERMINAL_STATUSES:
            status = "orphaned"
            ended_at = ended_at or now
        default_output_uri = self._output_uri(task_id) if output_path is not None else None
        default_output_offset = self._safe_file_size(output_path) if output_path is not None else 0
        if default_output_offset is None:
            default_output_offset = 0
        return TaskRecord(
            task_id=task_id,
            type=self._metadata_str(payload.get("type"), "subagent"),
            label=self._metadata_str(payload.get("label"), ""),
            description=self._metadata_str(payload.get("description"), ""),
            session_key=self._metadata_str(payload.get("session_key"), ""),
            status=status,
            started_at=self._metadata_str(payload.get("started_at"), now),
            updated_at=self._metadata_str(payload.get("updated_at"), now),
            ended_at=ended_at,
            tool_use_id=self._metadata_optional_str(payload.get("tool_use_id")),
            output_uri=self._metadata_optional_str(payload.get("output_uri")) or default_output_uri,
            output_offset=self._metadata_int(payload.get("output_offset"), default_output_offset),
            recent_activity=self._metadata_dict_list(payload.get("recent_activity")),
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            stop_reason=self._metadata_optional_str(payload.get("stop_reason")),
            error=self._metadata_optional_str(payload.get("error")),
        )

    def _write_metadata(self, record: TaskRecord) -> None:
        if not self._is_safe_task_id(record.task_id):
            return
        data = json.dumps(record.to_dict(), sort_keys=True).encode("utf-8")
        try:
            fd = self._open_no_symlink(
                self._metadata_path(record.task_id), os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            )
        except OSError as e:
            if e.errno == errno.ELOOP:
                return
            raise
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def _open_no_symlink(self, path: Path, flags: int):
        self._ensure_output_dir_safe(create=False)
        if path.is_symlink():
            raise OSError(errno.ELOOP, "symlink paths are not allowed", str(path))
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags, 0o600)

    def _ensure_output_dir_safe(self, *, create: bool) -> bool:
        pythinker_dir = self.workspace / ".pythinker"
        if pythinker_dir.is_symlink():
            raise OSError(errno.ELOOP, "symlink directories are not allowed", str(pythinker_dir))
        if create:
            pythinker_dir.mkdir(parents=True, exist_ok=True)
        elif not pythinker_dir.is_dir():
            return False

        if self.output_dir.is_symlink():
            raise OSError(errno.ELOOP, "symlink directories are not allowed", str(self.output_dir))
        if create:
            self.output_dir.mkdir(exist_ok=True)
        elif not self.output_dir.is_dir():
            return False

        if pythinker_dir.is_symlink() or self.output_dir.is_symlink():
            raise OSError(errno.ELOOP, "symlink directories are not allowed", str(self.output_dir))
        return True

    def _read_text_no_symlink(self, path: Path) -> str:
        fd = self._open_no_symlink(path, os.O_RDONLY)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError(errno.EISDIR, "metadata is not a regular file", str(path))
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return f.read()

    def _safe_file_size(self, path: Path) -> int | None:
        if path.is_symlink():
            return None
        try:
            file_stat = path.stat(follow_symlinks=False)
        except OSError:
            return None
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        return file_stat.st_size

    def _metadata_str(self, value: object, default: str) -> str:
        return value if isinstance(value, str) else default

    def _metadata_optional_str(self, value: object) -> str | None:
        return value if isinstance(value, str) else None

    def _metadata_int(self, value: object, default: int) -> int:
        return value if isinstance(value, int) else default

    def _metadata_dict_list(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            out.append({str(k): str(v) for k, v in item.items()})
        return out[-_MAX_RECENT_ACTIVITY:]

    def _trim_recent(self) -> None:
        if len(self._records) <= self.max_recent:
            return
        records = self.list_records()
        for record in records[self.max_recent :]:
            if record.status in _TERMINAL_STATUSES:
                self._records.pop(record.task_id, None)
                self._unindex_session(record)

    def _index_session(self, record: TaskRecord) -> None:
        if not record.session_key:
            return
        self._session_index.setdefault(record.session_key, set()).add(record.task_id)

    def _unindex_session(self, record: TaskRecord) -> None:
        task_ids = self._session_index.get(record.session_key)
        if task_ids is None:
            return
        task_ids.discard(record.task_id)
        if not task_ids:
            self._session_index.pop(record.session_key, None)

    def _is_safe_task_id(self, task_id: str) -> bool:
        return bool(task_id) and Path(task_id).name == task_id and "\\" not in task_id

    def _output_path(self, task_id: str) -> Path:
        return self.output_dir / f"{task_id}.txt"

    def _metadata_path(self, task_id: str) -> Path:
        return self.output_dir / f"{task_id}.json"

    def _output_uri(self, task_id: str) -> str:
        return f"task-output://{task_id}"
