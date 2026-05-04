from __future__ import annotations

from pathlib import Path

import pytest

from pythinker.agent.task_store import TaskStore
from pythinker.agent.tasks import TaskOutputRef, generate_task_id


def test_generate_task_id_uses_type_prefix() -> None:
    assert generate_task_id("subagent").startswith("a_")
    assert generate_task_id("shell").startswith("s_")
    assert generate_task_id("remote_agent").startswith("r_")
    assert generate_task_id("dream").startswith("d_")
    assert generate_task_id("workflow").startswith("w_")


def test_start_task_indexes_by_session_and_serializes(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
        tool_use_id="tool_123",
    )

    rows = store.list_records(session_key="websocket:abc")
    assert [row.task_id for row in rows] == [record.task_id]

    payload = record.to_dict()
    assert payload["task_id"] == record.task_id
    assert payload["type"] == "subagent"
    assert payload["status"] == "running"
    assert payload["session_key"] == "websocket:abc"
    assert payload["started_at"] == record.started_at
    assert payload["updated_at"] == record.updated_at
    assert payload["ended_at"] is None
    assert payload["tool_use_id"] == "tool_123"
    assert payload["recent_activity"] == []
    assert "output_path" not in payload


def test_start_task_can_reregister_supplied_task_id(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    first = store.start_task(
        task_type="subagent",
        label="first",
        description="old description",
        session_key="websocket:old",
        task_id="a_fixed",
    )
    second = store.start_task(
        task_type="subagent",
        label="second",
        description="new description",
        session_key="websocket:new",
        task_id="a_fixed",
        tool_use_id="tool_new",
    )

    assert first is second
    assert second.status == "running"
    assert second.label == "second"
    assert second.description == "new description"
    assert second.session_key == "websocket:new"
    assert second.tool_use_id == "tool_new"
    assert store.list_records(session_key="websocket:old") == []
    assert store.list_records(session_key="websocket:new") == [second]


def test_rejects_task_ids_that_escape_output_dir(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    escaped_output = tmp_path / ".pythinker" / "leak.txt"
    escaped_output.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid task id"):
        store.start_task(
            task_type="subagent",
            label="bad",
            description="bad",
            session_key="websocket:abc",
            task_id="../leak",
        )

    output = store.read_output("../leak")
    assert output.error == "task output not found"


def test_update_task_records_recent_activity_and_fields(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )

    updated = store.update_task(
        record.task_id,
        recent_activity={"name": "search", "detail": "searched files"},
        usage={"tokens": 10},
        stop_reason="tool_use",
        error="partial failure",
    )

    assert updated is record
    assert record.recent_activity == [{"name": "search", "detail": "searched files"}]
    assert record.usage == {"tokens": 10}
    assert record.stop_reason == "tool_use"
    assert record.error == "partial failure"


def test_update_task_appends_and_caps_recent_activity(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )

    for i in range(15):
        store.update_task(record.task_id, recent_activity={"name": "step", "detail": str(i)})

    assert record.recent_activity is not None
    assert len(record.recent_activity) == 10
    assert record.recent_activity[0] == {"name": "step", "detail": "5"}
    assert record.recent_activity[-1] == {"name": "step", "detail": "14"}


def test_append_and_read_output_tail(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )

    store.append_output(record.task_id, "line 1\n")
    store.append_output(record.task_id, "line 2\n")

    output = store.read_output(record.task_id, max_chars=128)
    assert output.task_id == record.task_id
    assert output.content == "line 1\nline 2\n"
    assert output.offset == len("line 1\nline 2\n".encode("utf-8"))
    assert output.truncated is False


def test_read_output_returns_bounded_tail(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )
    store.append_output(record.task_id, "abcdef")

    output = store.read_output(record.task_id, max_chars=3)

    assert output.content == "def"
    assert output.truncated is True


def test_read_output_with_zero_chars_returns_empty_tail(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )
    store.append_output(record.task_id, "abcdef")

    output = store.read_output(record.task_id, max_chars=0)

    assert output.content == ""
    assert output.truncated is True


def test_finish_and_cancel_update_status(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    done = store.start_task(
        task_type="subagent",
        label="done",
        description="finish",
        session_key="s:1",
    )
    cancelled = store.start_task(
        task_type="subagent",
        label="cancel",
        description="cancel",
        session_key="s:1",
    )

    store.finish_task(done.task_id, status="completed")
    store.cancel_task(cancelled.task_id)

    assert store.get(done.task_id).status == "completed"
    assert store.get(cancelled.task_id).status == "cancelled"
    assert store.list_records(session_key="s:1", include_terminal=False) == []


@pytest.mark.parametrize("status", ["completed", "failed", "orphaned"])
def test_cancel_task_does_not_overwrite_terminal_status(tmp_path: Path, status: str) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="done",
        description="terminal",
        session_key="s:1",
        task_id="a_terminal",
    )
    store.finish_task(record.task_id, status=status)

    cancelled = store.cancel_task(record.task_id)

    assert cancelled is record
    assert record.status == status


def test_finish_task_trims_terminal_records_after_active_tasks_complete(tmp_path: Path) -> None:
    store = TaskStore(tmp_path, max_recent=1)
    first = store.start_task(
        task_type="subagent",
        label="first",
        description="first",
        session_key="s:1",
    )
    second = store.start_task(
        task_type="subagent",
        label="second",
        description="second",
        session_key="s:1",
    )

    store.finish_task(first.task_id, status="completed")
    store.finish_task(second.task_id, status="completed")

    assert store.list_records() == [second]
    assert store.get(first.task_id) is None


def test_orphaned_outputs_are_loaded_on_startup(tmp_path: Path) -> None:
    output_dir = tmp_path / ".pythinker" / "task-results"
    output_dir.mkdir(parents=True)
    (output_dir / "a_existing.txt").write_text("saved output", encoding="utf-8")

    store = TaskStore(tmp_path)
    record = store.get("a_existing")

    assert record is not None
    assert record.status == "orphaned"
    assert store.read_output("a_existing", max_chars=100).content == "saved output"


def test_reusing_task_id_truncates_prior_output(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = store.start_task(
        task_type="subagent",
        label="first",
        description="old",
        session_key="s:1",
        task_id="a_reuse",
    )
    store.append_output(first.task_id, "OLD-OUTPUT")
    store.finish_task(first.task_id, status="completed")

    second = store.start_task(
        task_type="subagent",
        label="second",
        description="new",
        session_key="s:1",
        task_id="a_reuse",
    )

    assert second.output_uri is None
    assert second.output_offset == 0
    assert store.read_output(second.task_id).error == "task output not found"

    store.append_output(second.task_id, "NEW")
    output = store.read_output(second.task_id)
    assert output.content == "NEW"


def test_trim_removes_output_file_for_evicted_terminal_record(tmp_path: Path) -> None:
    store = TaskStore(tmp_path, max_recent=1)
    first = store.start_task(
        task_type="subagent",
        label="first",
        description="first",
        session_key="s:1",
        task_id="a_first",
    )
    store.append_output(first.task_id, "first output")
    store.finish_task(first.task_id, status="completed")
    second = store.start_task(
        task_type="subagent",
        label="second",
        description="second",
        session_key="s:1",
        task_id="a_second",
    )
    store.append_output(second.task_id, "second output")
    store.finish_task(second.task_id, status="completed")

    assert store.get("a_first") is None
    assert not (tmp_path / ".pythinker" / "task-results" / "a_first.txt").exists()
    assert (tmp_path / ".pythinker" / "task-results" / "a_second.txt").exists()


def test_orphaned_outputs_trim_keeps_newest_by_mtime(tmp_path: Path) -> None:
    import os

    output_dir = tmp_path / ".pythinker" / "task-results"
    output_dir.mkdir(parents=True)
    older = output_dir / "a_older.txt"
    newer = output_dir / "a_newer.txt"
    older.write_text("older", encoding="utf-8")
    newer.write_text("newer", encoding="utf-8")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    store = TaskStore(tmp_path, max_recent=1)

    rows = store.list_records()
    assert [r.task_id for r in rows] == ["a_newer"]


def test_missing_output_uses_error_shape(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    output = store.read_output("missing", max_chars=100)

    assert isinstance(output, TaskOutputRef)
    assert output.task_id == "missing"
    assert output.error == "task output not found"
