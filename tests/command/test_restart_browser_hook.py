"""Test that /restart closes the browser session for the active key."""

from unittest.mock import AsyncMock, MagicMock

from pythinker.command.builtin import cmd_restart
from pythinker.command.router import CommandContext


async def test_cmd_restart_closes_browser_session():
    fake_loop = MagicMock()
    fake_loop.close_browser_session = AsyncMock()
    # Other attrs cmd_restart touches — keep minimal stubs.
    fake_loop.sessions = MagicMock()
    fake_loop.sessions.delete = MagicMock()

    msg = MagicMock(channel="cli", chat_id="direct", content="/restart")
    ctx = CommandContext(
        msg=msg,
        session=MagicMock(),
        key="cli:direct",
        raw="/restart",
        args="",
        loop=fake_loop,
    )
    await cmd_restart(ctx)
    fake_loop.close_browser_session.assert_awaited_once_with("cli:direct")


async def test_cmd_restart_safe_when_no_browser_manager():
    """When the [browser] extra isn't installed, close_browser_session no-ops."""
    fake_loop = MagicMock()
    fake_loop.close_browser_session = AsyncMock()  # the method itself exists & no-ops
    fake_loop.sessions = MagicMock()
    fake_loop.sessions.delete = MagicMock()

    msg = MagicMock(channel="cli", chat_id="direct", content="/restart")
    ctx = CommandContext(
        msg=msg, session=MagicMock(), key="cli:direct", raw="/restart",
        args="", loop=fake_loop,
    )
    await cmd_restart(ctx)
    # Should have completed without raising.
