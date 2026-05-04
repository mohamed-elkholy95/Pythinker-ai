"""Tests for SubagentManager.list_statuses() — the live admin-surface read API."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.agent.hook import AgentHookContext
from pythinker.agent.subagent import SubagentManager, SubagentStatus
from pythinker.bus.queue import MessageBus


def _manager(tmp_path) -> SubagentManager:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4096,
        model="test-model",
    )


def test_list_statuses_empty(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr.list_statuses() == []


def test_list_statuses_active(tmp_path):
    mgr = _manager(tmp_path)
    # Inject a synthetic running status — avoids spawning a real LLM task.
    wall = time.time()
    mgr._task_statuses["abc123"] = SubagentStatus(
        task_id="abc123",
        label="dummy",
        task_description="do a thing",
        started_at=time.monotonic(),
        started_at_wall=wall,
        started_at_iso="2026-05-03T00:00:00+00:00",
        phase="awaiting_tools",
        iteration=2,
    )
    mgr._session_tasks["chan:c1"] = {"abc123"}

    rows = mgr.list_statuses()
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "abc123"
    assert row["session_key"] == "chan:c1"
    assert row["phase"] == "awaiting_tools"
    assert row["iteration"] == 2
    assert row["started_at_iso"] == "2026-05-03T00:00:00+00:00"
    assert "started_at" not in row, "raw monotonic value must not leak to callers"
    assert isinstance(row["elapsed_s"], float)
    assert row["elapsed_s"] >= 0


def test_list_statuses_unbound_session(tmp_path):
    """Statuses without a session entry get an empty session_key."""
    mgr = _manager(tmp_path)
    mgr._task_statuses["nosess"] = SubagentStatus(
        task_id="nosess",
        label="orphan",
        task_description="task",
        started_at=time.monotonic(),
    )
    rows = mgr.list_statuses()
    assert len(rows) == 1
    assert rows[0]["session_key"] == ""


@pytest.mark.asyncio
async def test_cancel_task_returns_false_for_unknown(tmp_path):
    mgr = _manager(tmp_path)
    assert await mgr.cancel_task("does-not-exist") is False


@pytest.mark.asyncio
async def test_cancel_task_cancels_running_task(tmp_path):
    mgr = _manager(tmp_path)

    async def _slow():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_slow())
    mgr._running_tasks["t1"] = task
    mgr._task_statuses["t1"] = SubagentStatus(
        task_id="t1",
        label="dummy",
        task_description="x",
        started_at=time.monotonic(),
    )
    mgr._session_tasks["s:1"] = {"t1"}

    cancelled = await mgr.cancel_task("t1")
    assert cancelled is True
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_cancel_task_returns_false_for_done_task(tmp_path):
    mgr = _manager(tmp_path)

    async def _quick():
        return "ok"

    task = asyncio.create_task(_quick())
    await task  # let it finish
    mgr._running_tasks["t2"] = task
    assert await mgr.cancel_task("t2") is False


async def test_spawn_creates_task_store_record_scoped_to_origin_session(tmp_path):
    mgr = _manager(tmp_path)
    mgr._run_subagent = AsyncMock()

    message = await mgr.spawn(
        "summarize logs",
        label="Summarize",
        origin_channel="slack",
        origin_chat_id="C1",
        session_key="unified:session",
    )

    records = mgr.task_store.list_records(session_key="unified:session")
    assert len(records) == 1
    record = records[0]
    assert record.task_id.startswith("a_")
    assert record.type == "subagent"
    assert record.label == "Summarize"
    assert record.description == "summarize logs"
    assert record.status == "running"
    assert f"id: {record.task_id}" in message
    mgr._run_subagent.assert_called_once()


async def test_spawn_falls_back_to_origin_channel_chat_session(tmp_path):
    mgr = _manager(tmp_path)
    mgr._run_subagent = AsyncMock()

    await mgr.spawn("summarize logs", origin_channel="telegram", origin_chat_id="42")

    records = mgr.task_store.list_records(session_key="telegram:42")
    assert len(records) == 1


async def test_run_subagent_finishes_completed_task_store_record(tmp_path):
    mgr = _manager(tmp_path)
    record = mgr.task_store.start_task(
        task_type="subagent",
        label="Summarize",
        description="summarize logs",
        session_key="slack:C1",
        task_id="a_test",
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        await spec.hook.after_iteration(AgentHookContext(
            iteration=2,
            messages=[],
            usage={"input_tokens": 3, "output_tokens": 4},
            tool_events=[{"name": "list_dir", "status": "ok", "detail": "listed files"}],
        ))
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done with logs",
            error=None,
            usage={"input_tokens": 5, "output_tokens": 6},
            tool_events=[{"name": "grep", "status": "ok", "detail": "found hits"}],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    status = SubagentStatus(
        task_id=record.task_id,
        label="Summarize",
        task_description="summarize logs",
        started_at=time.monotonic(),
    )

    await mgr._run_subagent(
        record.task_id,
        "summarize logs",
        "Summarize",
        {"channel": "slack", "chat_id": "C1", "session_key": "slack:C1"},
        status,
    )

    updated = mgr.task_store.get(record.task_id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.usage == {"input_tokens": 5, "output_tokens": 6}
    assert updated.stop_reason == "completed"
    assert updated.error is None
    activities = updated.recent_activity or []
    assert any(a.get("name") == "list_dir" and a.get("detail") == "listed files" for a in activities)
    assert any(a.get("name") == "grep" and a.get("detail") == "found hits" for a in activities)
    assert mgr.task_store.read_output(record.task_id).content == "done with logs"
    mgr._announce_result.assert_awaited_once()


async def test_run_subagent_finishes_failed_task_store_record_on_exception(tmp_path):
    mgr = _manager(tmp_path)
    record = mgr.task_store.start_task(
        task_type="subagent",
        label="Summarize",
        description="summarize logs",
        session_key="slack:C1",
        task_id="a_test",
    )
    mgr.runner.run = AsyncMock(side_effect=RuntimeError("boom"))
    mgr._announce_result = AsyncMock()
    status = SubagentStatus(
        task_id=record.task_id,
        label="Summarize",
        task_description="summarize logs",
        started_at=time.monotonic(),
    )

    await mgr._run_subagent(
        record.task_id,
        "summarize logs",
        "Summarize",
        {"channel": "slack", "chat_id": "C1", "session_key": "slack:C1"},
        status,
    )

    updated = mgr.task_store.get(record.task_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "boom"
    assert mgr.task_store.read_output(record.task_id).content == "Error: boom"
    mgr._announce_result.assert_awaited_once()


async def test_run_subagent_stores_result_error_as_failed_task_record(tmp_path):
    mgr = _manager(tmp_path)
    record = mgr.task_store.start_task(
        task_type="subagent",
        label="Summarize",
        description="summarize logs",
        session_key="slack:C1",
        task_id="a_test",
    )
    mgr.runner.run = AsyncMock(return_value=SimpleNamespace(
        stop_reason="empty_final_response",
        final_content=None,
        error="empty final response",
        usage={},
        tool_events=[],
    ))
    mgr._announce_result = AsyncMock()
    status = SubagentStatus(
        task_id=record.task_id,
        label="Summarize",
        task_description="summarize logs",
        started_at=time.monotonic(),
    )

    await mgr._run_subagent(
        record.task_id,
        "summarize logs",
        "Summarize",
        {"channel": "slack", "chat_id": "C1", "session_key": "slack:C1"},
        status,
    )

    updated = mgr.task_store.get(record.task_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.stop_reason == "empty_final_response"
    assert updated.error == "empty final response"
    assert mgr.task_store.read_output(record.task_id).content == "empty final response"
    mgr._announce_result.assert_awaited_once()


async def test_cancel_task_does_not_reclassify_completed_record_during_announce(tmp_path):
    mgr = _manager(tmp_path)
    record = mgr.task_store.start_task(
        task_type="subagent",
        label="Summarize",
        description="summarize logs",
        session_key="slack:C1",
        task_id="a_test",
    )
    announce_started = asyncio.Event()
    release_announce = asyncio.Event()

    async def fake_run(spec):
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done with logs",
            error=None,
            usage={},
            tool_events=[],
        )

    async def fake_announce(*args):
        announce_started.set()
        await release_announce.wait()

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    mgr._announce_result = AsyncMock(side_effect=fake_announce)
    task = asyncio.create_task(
        mgr._run_subagent(
            record.task_id,
            "summarize logs",
            "Summarize",
            {"channel": "slack", "chat_id": "C1", "session_key": "slack:C1"},
            SubagentStatus(
                task_id=record.task_id,
                label="Summarize",
                task_description="summarize logs",
                started_at=time.monotonic(),
            ),
        )
    )
    mgr._running_tasks[record.task_id] = task
    mgr._session_tasks["slack:C1"] = {record.task_id}

    await asyncio.wait_for(announce_started.wait(), timeout=1.0)
    before_cancel = mgr.task_store.get(record.task_id)
    assert before_cancel is not None
    assert before_cancel.status == "completed"
    assert mgr.task_store.read_output(record.task_id).content == "done with logs"

    cancel_return = await mgr.cancel_task(record.task_id)

    after_cancel = mgr.task_store.get(record.task_id)
    assert cancel_return is False
    assert after_cancel is not None
    assert after_cancel.status == "completed"
    release_announce.set()
    await task


async def test_cancel_task_marks_task_store_record_cancelled(tmp_path):
    mgr = _manager(tmp_path)
    record = mgr.task_store.start_task(
        task_type="subagent",
        label="Slow",
        description="wait",
        session_key="slack:C1",
        task_id="a_test",
    )

    async def _slow():
        await asyncio.sleep(60)

    task = asyncio.create_task(_slow())
    mgr._running_tasks[record.task_id] = task

    assert await mgr.cancel_task(record.task_id) is True
    assert mgr.task_store.get(record.task_id).status == "cancelled"


async def test_cancel_by_session_marks_task_store_records_cancelled(tmp_path):
    mgr = _manager(tmp_path)
    first = mgr.task_store.start_task(
        task_type="subagent",
        label="One",
        description="wait one",
        session_key="slack:C1",
        task_id="a_one",
    )
    second = mgr.task_store.start_task(
        task_type="subagent",
        label="Two",
        description="wait two",
        session_key="slack:C1",
        task_id="a_two",
    )

    async def _slow():
        await asyncio.sleep(60)

    task_one = asyncio.create_task(_slow())
    task_two = asyncio.create_task(_slow())
    mgr._running_tasks[first.task_id] = task_one
    mgr._running_tasks[second.task_id] = task_two
    mgr._session_tasks["slack:C1"] = {first.task_id, second.task_id}

    assert await mgr.cancel_by_session("slack:C1") == 2
    assert mgr.task_store.get(first.task_id).status == "cancelled"
    assert mgr.task_store.get(second.task_id).status == "cancelled"
