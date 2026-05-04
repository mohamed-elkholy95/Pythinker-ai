from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
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


def test_rejects_symlinked_pythinker_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".pythinker").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="symlink"):
        TaskStore(tmp_path)

    assert not (outside / "task-results").exists()


def test_rejects_symlinked_task_results_directory(tmp_path: Path) -> None:
    pythinker_dir = tmp_path / ".pythinker"
    pythinker_dir.mkdir()
    outside = tmp_path / "outside-results"
    outside.mkdir()
    (pythinker_dir / "task-results").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="symlink"):
        TaskStore(tmp_path)

    assert list(outside.iterdir()) == []


def test_swapped_task_results_symlink_does_not_expose_output_or_metadata(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="safe",
        description="inside workspace",
        session_key="websocket:safe",
        task_id="a_safe",
    )
    store.append_output(record.task_id, "safe output")
    shutil.rmtree(store.output_dir)

    outside = tmp_path / "outside-results"
    outside.mkdir()
    (outside / "a_safe.txt").write_text("secret output", encoding="utf-8")
    (outside / "a_secret.json").write_text(
        json.dumps(
            {
                "task_id": "a_secret",
                "type": "subagent",
                "label": "secret",
                "description": "outside metadata",
                "session_key": "websocket:secret",
                "status": "completed",
                "started_at": "2026-05-03T00:00:00+00:00",
                "updated_at": "2026-05-03T00:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    store.output_dir.symlink_to(outside, target_is_directory=True)

    output = store.read_output("a_safe", max_chars=100)
    assert output.error == "task output not found"
    assert "secret output" not in output.content

    store._records.clear()
    store._session_index.clear()
    store._load_orphaned_outputs()

    assert store.get("a_secret") is None
    assert store.list_records(session_key="websocket:secret") == []


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


def test_task_record_serializes_usage_as_json_safe_payload(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )
    store.update_task(
        record.task_id,
        usage={
            "path": tmp_path,
            "when": datetime(2026, 5, 3, tzinfo=UTC),
            "nested": [b"bytes"],
        },
    )

    payload = record.to_dict()

    json.dumps(payload)
    assert payload["usage"] == {
        "path": str(tmp_path),
        "when": "2026-05-03T00:00:00+00:00",
        "nested": ["b'bytes'"],
    }


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


def test_read_output_rejects_symlinked_output_file(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
        task_id="a_linked",
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("secret output", encoding="utf-8")
    store._output_path(record.task_id).symlink_to(secret)

    output = store.read_output(record.task_id, max_chars=100)

    assert output.error == "task output not found"


def test_append_output_does_not_follow_existing_symlinked_output_file(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
        task_id="a_append_link",
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    store._output_path(record.task_id).symlink_to(secret)

    updated = store.append_output(record.task_id, "\nleaked")

    assert updated is record
    assert secret.read_text(encoding="utf-8") == "secret"
    assert store.read_output(record.task_id).error == "task output not found"


def test_metadata_symlink_is_not_followed_when_writing(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    secret = tmp_path / "metadata-secret.json"
    secret.write_text("original", encoding="utf-8")
    store._metadata_path("a_metadata_link").symlink_to(secret)

    store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
        task_id="a_metadata_link",
    )

    assert secret.read_text(encoding="utf-8") == "original"


def test_metadata_backed_outputs_reload_with_session_and_safe_status(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    completed = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
        task_id="a_completed",
        tool_use_id="tool_123",
    )
    running = store.start_task(
        task_type="subagent",
        label="draft",
        description="still running at shutdown",
        session_key="websocket:abc",
        task_id="a_running",
    )
    store.append_output(completed.task_id, "saved output")
    store.append_output(running.task_id, "partial output")
    store.finish_task(completed.task_id, status="completed", stop_reason="done")

    reloaded = TaskStore(tmp_path)
    rows = {record.task_id: record for record in reloaded.list_records(session_key="websocket:abc")}

    assert set(rows) == {"a_completed", "a_running"}
    assert rows["a_completed"].status == "completed"
    assert rows["a_completed"].label == "research"
    assert rows["a_completed"].description == "inspect module"
    assert rows["a_completed"].type == "subagent"
    assert rows["a_completed"].started_at == completed.started_at
    assert rows["a_completed"].updated_at == completed.updated_at
    assert rows["a_completed"].ended_at == completed.ended_at
    assert rows["a_completed"].tool_use_id == "tool_123"
    assert rows["a_completed"].stop_reason == "done"
    assert rows["a_completed"].output_uri == "task-output://a_completed"
    assert rows["a_completed"].output_offset == len("saved output".encode("utf-8"))
    assert rows["a_running"].status == "orphaned"
    assert rows["a_running"].session_key == "websocket:abc"
    assert reloaded.read_output("a_completed", max_chars=100).content == "saved output"
    assert reloaded.read_output("a_running", max_chars=100).content == "partial output"


def test_metadata_only_running_task_reloads_as_session_orphan(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="restart before output",
        session_key="websocket:abc",
        task_id="a_metadata_only",
    )

    reloaded = TaskStore(tmp_path)
    rows = reloaded.list_records(session_key="websocket:abc")

    assert len(rows) == 1
    reloaded_record = rows[0]
    assert reloaded_record.task_id == record.task_id
    assert reloaded_record.status == "orphaned"
    assert reloaded_record.session_key == "websocket:abc"
    assert reloaded_record.label == "research"
    assert reloaded_record.description == "restart before output"
    assert reloaded.read_output(record.task_id).error == "task output not found"


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


def test_read_output_tail_does_not_read_entire_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = TaskStore(tmp_path)
    record = store.start_task(
        task_type="subagent",
        label="research",
        description="inspect module",
        session_key="websocket:abc",
    )
    store.append_output(record.task_id, "abcdef")

    def fail_read_text(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError("read_output should read a bounded tail")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

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


def test_symlinked_orphaned_outputs_are_ignored_on_startup(tmp_path: Path) -> None:
    output_dir = tmp_path / ".pythinker" / "task-results"
    output_dir.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret output", encoding="utf-8")
    (output_dir / "a_linked.txt").symlink_to(secret)

    store = TaskStore(tmp_path)

    assert store.get("a_linked") is None
    assert store.read_output("a_linked", max_chars=100).error == "task output not found"


def test_metadata_symlink_is_not_followed_when_loading(tmp_path: Path) -> None:
    output_dir = tmp_path / ".pythinker" / "task-results"
    output_dir.mkdir(parents=True)
    outside_metadata = tmp_path / "metadata.json"
    outside_metadata.write_text(
        json.dumps(
            {
                "task_id": "a_metadata_link",
                "type": "subagent",
                "label": "secret",
                "description": "secret",
                "session_key": "websocket:abc",
                "status": "completed",
                "started_at": "2026-05-03T10:00:00+00:00",
                "updated_at": "2026-05-03T10:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "a_metadata_link.json").symlink_to(outside_metadata)

    store = TaskStore(tmp_path)

    assert store.get("a_metadata_link") is None


def test_orphaned_outputs_are_trimmed_on_startup(tmp_path: Path) -> None:
    output_dir = tmp_path / ".pythinker" / "task-results"
    output_dir.mkdir(parents=True)
    (output_dir / "a_one.txt").write_text("one", encoding="utf-8")
    (output_dir / "a_two.txt").write_text("two", encoding="utf-8")

    store = TaskStore(tmp_path, max_recent=1)

    assert len(store.list_records()) == 1


def test_missing_output_uses_error_shape(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    output = store.read_output("missing", max_chars=100)

    assert isinstance(output, TaskOutputRef)
    assert output.task_id == "missing"
    assert output.error == "task output not found"
