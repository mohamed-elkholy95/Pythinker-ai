from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from pythinker.agent.tasks import TaskOutputRef, TaskRecord
from pythinker.bus.events import InboundMessage
from pythinker.channels.telegram import _markdown_to_telegram_html
from pythinker.command.builtin import cmd_task_output, cmd_task_stop, cmd_tasks
from pythinker.command.router import CommandContext


def _ctx(raw: str, *, args: str = "", loop: object | None = None) -> CommandContext:
    msg = InboundMessage(
        channel="websocket",
        sender_id="user1",
        chat_id="chat1",
        content=raw,
        metadata={"source": "test"},
    )
    return CommandContext(
        msg=msg,
        session=None,
        key=msg.session_key,
        raw=raw,
        args=args,
        loop=loop or SimpleNamespace(),
    )


def _record(task_id: str, *, label: str, status: str, session_key: str) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        type="subagent",
        label=label,
        description="inspect files",
        session_key=session_key,
        status=status,
        started_at="2026-05-03T10:00:00+00:00",
        updated_at="2026-05-03T10:01:00+00:00",
    )


async def test_tasks_lists_session_records_with_text_metadata() -> None:
    task_store = MagicMock()
    task_store.list_records.return_value = [
        _record("a_one", label="research `docs`", status="running", session_key="websocket:chat1"),
    ]
    loop = SimpleNamespace(task_store=task_store)

    out = await cmd_tasks(_ctx("/tasks", loop=loop))

    task_store.list_records.assert_called_once_with(session_key="websocket:chat1")
    assert "a_one" in out.content
    assert "research \\`docs\\`" in out.content
    assert "running" in out.content
    assert out.metadata == {"source": "test", "render_as": "text"}


async def test_tasks_reports_empty_session() -> None:
    task_store = MagicMock()
    task_store.list_records.return_value = []
    loop = SimpleNamespace(task_store=task_store)

    out = await cmd_tasks(_ctx("/tasks", loop=loop))

    assert out.content == "No tasks for this session."
    assert out.metadata["render_as"] == "text"


async def test_task_output_reads_bounded_output() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_one", label="research", status="running", session_key="websocket:chat1"
    )
    task_store.read_output.return_value = TaskOutputRef(
        task_id="a_one",
        content="line 1\nline 2\n",
        offset=14,
        truncated=False,
    )
    loop = SimpleNamespace(task_store=task_store)

    out = await cmd_task_output(_ctx("/task-output a_one", args="a_one", loop=loop))

    task_store.read_output.assert_called_once_with("a_one", max_chars=16000)
    assert out.content.startswith("## Task Output `a_one`")
    assert "line 1\nline 2" in out.content
    assert out.metadata["render_as"] == "text"


async def test_task_output_with_backticks_stays_in_telegram_code_block() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_one", label="research", status="running", session_key="websocket:chat1"
    )
    task_store.read_output.return_value = TaskOutputRef(
        task_id="a_one",
        content="before\n```\nafter\n",
        offset=17,
        truncated=False,
    )
    loop = SimpleNamespace(task_store=task_store)

    out = await cmd_task_output(_ctx("/task-output a_one", args="a_one", loop=loop))

    assert "````" not in out.content
    assert out.content.count("```") == 2
    html = _markdown_to_telegram_html(out.content)
    assert "<pre><code>before" in html
    assert "after\n\n</code></pre>" in html


async def test_task_output_denies_other_session_task_without_reading_output() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_other", label="secret", status="running", session_key="websocket:other"
    )
    loop = SimpleNamespace(task_store=task_store)

    out = await cmd_task_output(_ctx("/task-output a_other", args="a_other", loop=loop))

    task_store.read_output.assert_not_called()
    assert out.content == "Task output unavailable for `a_other`: task output not found"
    assert out.metadata["render_as"] == "text"


async def test_task_output_reads_orphaned_output_with_blank_session_key() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record("a_orphan", label="", status="orphaned", session_key="")
    task_store.read_output.return_value = TaskOutputRef(
        task_id="a_orphan",
        content="recovered\n",
        offset=10,
        truncated=False,
    )
    loop = SimpleNamespace(task_store=task_store)

    out = await cmd_task_output(_ctx("/task-output a_orphan", args="a_orphan", loop=loop))

    task_store.read_output.assert_called_once_with("a_orphan", max_chars=16000)
    assert out.content.startswith("## Task Output `a_orphan`")
    assert "recovered" in out.content
    assert out.metadata["render_as"] == "text"


async def test_task_output_without_id_returns_usage() -> None:
    out = await cmd_task_output(_ctx("/task-output"))

    assert out.content == "Usage: `/task-output <task_id>`"
    assert out.metadata["render_as"] == "text"


async def test_task_stop_reports_stopped_when_cancelled() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_one", label="research", status="running", session_key="websocket:chat1"
    )
    subagents = SimpleNamespace(cancel_task=AsyncMock(return_value=True))
    loop = SimpleNamespace(task_store=task_store, subagents=subagents)

    out = await cmd_task_stop(_ctx("/task-stop a_one", args="a_one", loop=loop))

    subagents.cancel_task.assert_awaited_once_with("a_one")
    assert out.content == "Stopped task `a_one`."
    assert out.metadata["render_as"] == "text"


async def test_task_stop_reports_not_running_when_cancel_returns_false() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_one", label="research", status="running", session_key="websocket:chat1"
    )
    subagents = SimpleNamespace(cancel_task=AsyncMock(return_value=False))
    loop = SimpleNamespace(task_store=task_store, subagents=subagents)

    out = await cmd_task_stop(_ctx("/task-stop a_one", args="a_one", loop=loop))

    subagents.cancel_task.assert_awaited_once_with("a_one")
    assert out.content == "No running task `a_one`."
    assert out.metadata["render_as"] == "text"


async def test_task_stop_denies_other_session_task_without_cancelling() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_other", label="secret", status="running", session_key="websocket:other"
    )
    subagents = SimpleNamespace(cancel_task=AsyncMock(return_value=True))
    loop = SimpleNamespace(task_store=task_store, subagents=subagents)

    out = await cmd_task_stop(_ctx("/task-stop a_other", args="a_other", loop=loop))

    subagents.cancel_task.assert_not_awaited()
    assert out.content == "No running task `a_other`."
    assert out.metadata["render_as"] == "text"


async def test_task_stop_does_not_cancel_orphaned_same_session_task() -> None:
    task_store = MagicMock()
    task_store.get.return_value = _record(
        "a_orphan", label="stale", status="orphaned", session_key="websocket:chat1"
    )
    subagents = SimpleNamespace(cancel_task=AsyncMock(return_value=True))
    loop = SimpleNamespace(task_store=task_store, subagents=subagents)

    out = await cmd_task_stop(_ctx("/task-stop a_orphan", args="a_orphan", loop=loop))

    subagents.cancel_task.assert_not_awaited()
    assert out.content == "No running task `a_orphan`."
    assert out.metadata["render_as"] == "text"
