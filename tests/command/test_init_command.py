"""Tests for the ``/init`` slash command.

Phase 4 of `.agents/plans/2026-05-05-coding-prompt-uplift.md`. ``/init``
renders ``agent/init_agents_md.md`` and republishes it as a fresh
``InboundMessage`` so the agent's normal tool-use loop walks the project
root, identifies the stack, and writes a tuned ``AGENTS.md``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from pythinker.bus.events import InboundMessage
from pythinker.command.builtin import cmd_init
from pythinker.command.router import CommandContext


def _build_ctx(channel: str = "cli", chat_id: str = "direct") -> CommandContext:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    loop = SimpleNamespace(bus=bus)
    msg = InboundMessage(
        channel=channel,
        chat_id=chat_id,
        sender_id="u",
        content="/init",
        session_key_override=f"{channel}:{chat_id}",
    )
    return CommandContext(loop=loop, msg=msg, key=msg.session_key, session=None, raw="/init")


async def test_init_publishes_inbound_message():
    ctx = _build_ctx()
    await cmd_init(ctx)
    ctx.loop.bus.publish_inbound.assert_awaited_once()
    published = ctx.loop.bus.publish_inbound.await_args.args[0]
    assert isinstance(published, InboundMessage)
    assert published.channel == "cli"
    assert published.chat_id == "direct"
    assert published.session_key_override == "cli:direct"


async def test_init_published_content_is_the_template_directive():
    """The injected message must instruct the agent to walk + write AGENTS.md."""
    ctx = _build_ctx()
    await cmd_init(ctx)
    published = ctx.loop.bus.publish_inbound.await_args.args[0]
    body = published.content
    # Marker phrases that should survive future template edits without
    # being so brittle they fail on every wording tweak.
    assert "AGENTS.md" in body
    assert "glob" in body and "grep" in body
    assert "write_file" in body


async def test_init_metadata_marks_injected_event():
    """Downstream telemetry / debug should see the synthetic origin."""
    ctx = _build_ctx()
    await cmd_init(ctx)
    published = ctx.loop.bus.publish_inbound.await_args.args[0]
    assert (published.metadata or {}).get("injected_event") == "init_agents_md"


async def test_init_returns_none_so_no_outbound_message_is_emitted():
    """Like /regenerate, /init replaces the user-typed message rather than
    chatting back. The handler must return None so the router does not emit
    a competing OutboundMessage."""
    ctx = _build_ctx()
    result = await cmd_init(ctx)
    assert result is None


def test_init_is_registered_with_metadata():
    from pythinker.command import CommandRouter, register_builtin_commands
    from pythinker.command.metadata import BUILTIN_COMMAND_METADATA

    router = CommandRouter()
    register_builtin_commands(router)
    assert "/init" in router._exact

    declared = {m.name for m in BUILTIN_COMMAND_METADATA}
    assert "/init" in declared
