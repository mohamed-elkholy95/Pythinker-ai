"""``/init`` slash command — generate a project-tuned ``AGENTS.md``.

Phase 4 of `.agents/plans/2026-05-05-coding-prompt-uplift.md`.

The handler renders a static prompt (`agent/init_agents_md.md`) and
republishes it as a fresh ``InboundMessage`` so the next agent turn
runs through the normal tool-use loop and writes ``AGENTS.md`` itself.
No checkpoint-key changes — this is a synchronous user-message
injection, not a new turn-state.
"""

from __future__ import annotations

from pythinker.bus.events import InboundMessage
from pythinker.command.router import CommandContext
from pythinker.utils.prompt_templates import render_template


async def cmd_init(ctx: CommandContext) -> None:
    """Inject the AGENTS.md generation prompt as a fresh user message."""
    prompt = render_template("agent/init_agents_md.md", strip=True)
    msg = ctx.msg
    await ctx.loop.bus.publish_inbound(
        InboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=prompt,
            sender_id=msg.sender_id,
            session_key_override=msg.session_key,
            metadata={
                **dict(msg.metadata or {}),
                "injected_event": "init_agents_md",
            },
        )
    )
    return None
