"""Tests for the /regenerate and /edit priority command handlers.

These commands are dispatched from ``AgentLoop.run`` *before* the
per-session dispatch lock is acquired, but each handler must internally
acquire that same lock before mutating ``session.messages``. That
serializes them with any in-flight turn the channel may have raced.

The fixture below builds a minimal fake AgentLoop that exposes only the
attributes the handlers touch: ``_session_locks``, ``_active_tasks``,
``subagents``, ``sessions`` (a session manager), and ``bus``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from pythinker.bus.events import InboundMessage
from pythinker.command.builtin import cmd_edit, cmd_regenerate
from pythinker.command.router import CommandContext


def _build_session(messages: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(messages=list(messages))


def _build_loop(session: SimpleNamespace) -> SimpleNamespace:
    """Build a minimal fake loop that the priority handlers can drive."""
    sessions = MagicMock()
    sessions.get_or_create = MagicMock(return_value=session)
    sessions.save = MagicMock()
    sessions.truncate_after_user_index = MagicMock()

    bus = SimpleNamespace(publish_inbound=AsyncMock())

    subagents = MagicMock()
    subagents.cancel_by_session = AsyncMock(return_value=0)

    loop = SimpleNamespace(
        _session_locks={},
        _active_tasks={},
        subagents=subagents,
        sessions=sessions,
        bus=bus,
    )

    async def _cancel_active_tasks(key: str) -> int:
        # Match AgentLoop._cancel_active_tasks: drain the per-key task list
        # and the subagent registry. Tests can override this to record calls.
        loop._active_tasks.pop(key, None)
        return 0

    loop._cancel_active_tasks = _cancel_active_tasks
    return loop


def _make_inbound(content: str, *, metadata: dict | None = None) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="client-x",
        chat_id="abcd-1234",
        content=content,
        metadata=dict(metadata or {}),
    )


def _ctx_for(msg: InboundMessage, loop: SimpleNamespace) -> CommandContext:
    return CommandContext(
        msg=msg, session=None, key=msg.session_key, raw=msg.content, loop=loop,
    )


async def test_cmd_regenerate_truncates_and_republishes() -> None:
    """Happy path: drop the trailing assistant turn and republish the prior
    user message as a fresh InboundMessage."""
    session = _build_session([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "stale reply"},
    ])
    loop = _build_loop(session)
    msg = _make_inbound("/regenerate")

    await cmd_regenerate(_ctx_for(msg, loop))

    loop.sessions.truncate_after_user_index.assert_called_once_with(
        "websocket:abcd-1234", user_msg_index=0,
    )
    assert loop.bus.publish_inbound.await_count == 1
    republished = loop.bus.publish_inbound.await_args.args[0]
    assert republished.content == "hello"
    assert republished.chat_id == "abcd-1234"
    assert republished.channel == "websocket"
    assert republished.metadata.get("regenerated") is True

    # The per-session lock entry was created (and is currently free again).
    lock = loop._session_locks["websocket:abcd-1234"]
    assert isinstance(lock, asyncio.Lock)
    assert not lock.locked()


async def test_cmd_regenerate_with_no_user_messages_is_noop() -> None:
    """Empty session: no exception, no truncate, no publish."""
    session = _build_session([])
    loop = _build_loop(session)
    msg = _make_inbound("/regenerate")

    result = await cmd_regenerate(_ctx_for(msg, loop))

    assert result is None
    loop.sessions.truncate_after_user_index.assert_not_called()
    assert loop.bus.publish_inbound.await_count == 0


async def test_cmd_regenerate_picks_last_user_message() -> None:
    """Multi-turn history: regenerate restarts from the LAST user turn."""
    session = _build_session([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": "ok"},
    ])
    loop = _build_loop(session)
    msg = _make_inbound("/regenerate")

    await cmd_regenerate(_ctx_for(msg, loop))

    # user_indices == [0, 2], so the last user message is at user_msg_index=1.
    loop.sessions.truncate_after_user_index.assert_called_once_with(
        "websocket:abcd-1234", user_msg_index=1,
    )
    republished = loop.bus.publish_inbound.await_args.args[0]
    assert republished.content == "again"


async def test_cmd_edit_rewrites_and_republishes() -> None:
    """Happy path: rewrite the targeted user message in place, persist,
    truncate, and republish the new content."""
    session = _build_session([
        {"role": "user", "content": "old text"},
        {"role": "assistant", "content": "stale reply"},
    ])
    loop = _build_loop(session)
    msg = _make_inbound(
        "/edit",
        metadata={"edit_user_msg_index": 0, "edit_content": "new text"},
    )

    await cmd_edit(_ctx_for(msg, loop))

    assert session.messages[0]["content"] == "new text"
    loop.sessions.save.assert_called_once_with(session)
    loop.sessions.truncate_after_user_index.assert_called_once_with(
        "websocket:abcd-1234", user_msg_index=0,
    )
    assert loop.bus.publish_inbound.await_count == 1
    republished = loop.bus.publish_inbound.await_args.args[0]
    assert republished.content == "new text"
    assert republished.metadata.get("edited") is True
    # edit_* keys must be stripped from the forwarded metadata.
    assert "edit_user_msg_index" not in republished.metadata
    assert "edit_content" not in republished.metadata


async def test_cmd_edit_with_missing_metadata_is_noop() -> None:
    """No edit_* metadata at all: silent no-op (channel pre-validated)."""
    session = _build_session([{"role": "user", "content": "hello"}])
    loop = _build_loop(session)
    msg = _make_inbound("/edit")

    result = await cmd_edit(_ctx_for(msg, loop))

    assert result is None
    assert session.messages[0]["content"] == "hello"
    loop.sessions.save.assert_not_called()
    loop.sessions.truncate_after_user_index.assert_not_called()
    assert loop.bus.publish_inbound.await_count == 0


async def test_cmd_edit_with_wrong_type_metadata_is_noop() -> None:
    """edit_user_msg_index that isn't an int: silent no-op."""
    session = _build_session([{"role": "user", "content": "hello"}])
    loop = _build_loop(session)
    msg = _make_inbound(
        "/edit",
        metadata={"edit_user_msg_index": "0", "edit_content": "x"},
    )

    result = await cmd_edit(_ctx_for(msg, loop))

    assert result is None
    loop.sessions.save.assert_not_called()
    loop.sessions.truncate_after_user_index.assert_not_called()
    assert loop.bus.publish_inbound.await_count == 0


async def test_cmd_edit_with_empty_content_is_noop() -> None:
    """Whitespace-only edit_content: silent no-op (channel emits the user
    error; this guard is defense in depth)."""
    session = _build_session([{"role": "user", "content": "hello"}])
    loop = _build_loop(session)
    msg = _make_inbound(
        "/edit",
        metadata={"edit_user_msg_index": 0, "edit_content": "   "},
    )

    result = await cmd_edit(_ctx_for(msg, loop))

    assert result is None
    loop.sessions.save.assert_not_called()
    loop.sessions.truncate_after_user_index.assert_not_called()
    assert loop.bus.publish_inbound.await_count == 0


async def test_cmd_edit_with_out_of_range_index_is_noop() -> None:
    """edit_user_msg_index pointing past the end of user_positions: no-op."""
    session = _build_session([{"role": "user", "content": "hello"}])
    loop = _build_loop(session)
    msg = _make_inbound(
        "/edit",
        metadata={"edit_user_msg_index": 5, "edit_content": "new"},
    )

    result = await cmd_edit(_ctx_for(msg, loop))

    assert result is None
    # The original message must not have been overwritten.
    assert session.messages[0]["content"] == "hello"
    loop.sessions.save.assert_not_called()
    loop.sessions.truncate_after_user_index.assert_not_called()
    assert loop.bus.publish_inbound.await_count == 0


async def test_cmd_regenerate_cancels_active_tasks_first() -> None:
    """Even when the active-task list is empty, the handler must invoke
    ``_cancel_active_tasks`` to drain any in-flight turn before mutating."""
    session = _build_session([{"role": "user", "content": "hi"}])
    loop = _build_loop(session)
    cancel_calls: list[str] = []

    async def _record_cancel(key: str) -> int:
        cancel_calls.append(key)
        return 0

    loop._cancel_active_tasks = _record_cancel  # type: ignore[attr-defined]

    msg = _make_inbound("/regenerate")
    await cmd_regenerate(_ctx_for(msg, loop))

    assert cancel_calls == ["websocket:abcd-1234"]


async def test_cmd_edit_cancels_active_tasks_first() -> None:
    """Same contract as regenerate — cancel before mutate."""
    session = _build_session([{"role": "user", "content": "hi"}])
    loop = _build_loop(session)
    cancel_calls: list[str] = []

    async def _record_cancel(key: str) -> int:
        cancel_calls.append(key)
        return 0

    loop._cancel_active_tasks = _record_cancel  # type: ignore[attr-defined]

    msg = _make_inbound(
        "/edit",
        metadata={"edit_user_msg_index": 0, "edit_content": "new"},
    )
    await cmd_edit(_ctx_for(msg, loop))

    assert cancel_calls == ["websocket:abcd-1234"]
