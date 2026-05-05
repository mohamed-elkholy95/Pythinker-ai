"""Task-related built-in slash command handlers."""

from __future__ import annotations

from pythinker.bus.events import OutboundMessage
from pythinker.command.builtins.format import _fenced_text, _format_task_row
from pythinker.command.router import CommandContext


def _task_id_from_args(args: str) -> str:
    """Return the first whitespace-delimited task id argument."""
    parts = args.strip().split(maxsplit=1)
    return parts[0] if parts else ""


def _task_record_for_session(ctx: CommandContext, task_id: str):
    record = ctx.loop.task_store.get(task_id)
    if record is None or record.session_key != ctx.key or record.status not in {"pending", "running"}:
        return None
    return record


def _task_output_record_for_session(ctx: CommandContext, task_id: str):
    record = ctx.loop.task_store.get(task_id)
    if record is None:
        return None
    if record.status == "orphaned" and not record.session_key:
        return record
    if record.session_key != ctx.key:
        return None
    return record


async def cmd_tasks(ctx: CommandContext) -> OutboundMessage:
    """List task records for the current session."""
    records = ctx.loop.task_store.list_records(session_key=ctx.key)
    if not records:
        content = "No tasks for this session."
    else:
        lines = ["## Tasks", ""]
        lines.extend(_format_task_row(record) for record in records)
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_task_output(ctx: CommandContext) -> OutboundMessage:
    """Show the latest bounded output for a task."""
    task_id = _task_id_from_args(ctx.args)
    if not task_id:
        content = "Usage: `/task-output <task_id>`"
    elif _task_output_record_for_session(ctx, task_id) is None:
        content = f"Task output unavailable for `{task_id}`: task output not found"
    else:
        output = ctx.loop.task_store.read_output(task_id, max_chars=16000)
        if output.error:
            content = f"Task output unavailable for `{task_id}`: {output.error}"
        elif not output.content:
            content = f"No output available for task `{task_id}`."
        else:
            content = f"## Task Output `{task_id}`\n\n{_fenced_text(output.content)}"
            if output.truncated:
                content += "\n\n_Output truncated to the latest 16000 characters._"
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_task_stop(ctx: CommandContext) -> OutboundMessage:
    """Stop a running autonomous task by id."""
    task_id = _task_id_from_args(ctx.args)
    if not task_id:
        content = "Usage: `/task-stop <task_id>`"
    elif _task_record_for_session(ctx, task_id) is None:
        content = f"No running task `{task_id}`."
    elif await ctx.loop.subagents.cancel_task(task_id):
        content = f"Stopped task `{task_id}`."
    else:
        content = f"No running task `{task_id}`."
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )
