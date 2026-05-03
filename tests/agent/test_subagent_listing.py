"""Tests for SubagentManager.list_statuses() — the live admin-surface read API."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

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


import asyncio  # noqa: E402

import pytest  # noqa: E402


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
