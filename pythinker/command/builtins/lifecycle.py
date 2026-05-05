"""Lifecycle and turn-control built-in slash command handlers.

Includes session-state mutation handlers (``cmd_new``, ``cmd_regenerate``,
``cmd_edit``) and process-control handlers (``cmd_stop``, ``cmd_status``,
``cmd_help``, ``build_help_text``).

The ``_cmd_restart_impl`` and ``_cmd_upgrade_impl`` helpers take ``asyncio``
and ``os`` modules as arguments so the thin wrappers in
``pythinker.command.builtin`` can keep ``patch("pythinker.command.builtin.asyncio")``
and ``patch("pythinker.command.builtin.os.execv")`` working — see the
project's split-PR checklist for the patch-target contract.
"""

from __future__ import annotations

import asyncio
import sys

from pythinker import __version__
from pythinker.bus.events import InboundMessage, OutboundMessage
from pythinker.command.router import CommandContext
from pythinker.utils.helpers import build_status_content
from pythinker.utils.restart import set_restart_notice_to_env


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(msg.session_key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def _cmd_restart_impl(asyncio_mod, os_mod, ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv.

    ``asyncio_mod`` and ``os_mod`` are injected by the compatibility wrapper in
    ``pythinker.command.builtin`` so tests that patch
    ``pythinker.command.builtin.asyncio`` / ``pythinker.command.builtin.os.execv``
    affect this handler's behavior.
    """
    msg = ctx.msg
    set_restart_notice_to_env(channel=msg.channel, chat_id=msg.chat_id)

    async def _do_restart():
        await asyncio_mod.sleep(1)
        os_mod.execv(sys.executable, [sys.executable, "-m", "pythinker"] + sys.argv[1:])

    asyncio_mod.create_task(_do_restart())
    if ctx.loop is not None and hasattr(ctx.loop, "close_browser_session"):
        await ctx.loop.close_browser_session(ctx.key)
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def _cmd_upgrade_impl(asyncio_mod, os_mod, ctx: CommandContext) -> OutboundMessage:
    """Check PyPI and upgrade pythinker in place; restart on success.

    See ``_cmd_restart_impl`` for the ``asyncio_mod`` / ``os_mod`` injection
    rationale — it mirrors the same patch-target contract for forward
    compatibility with future tests.
    """
    import subprocess

    from filelock import FileLock
    from filelock import Timeout as FileLockTimeout

    from pythinker.config.paths import get_update_dir
    from pythinker.utils.update import (
        check_for_update,
        suggested_upgrade_command,
        upgrade_command,
    )

    msg = ctx.msg
    metadata = {**dict(msg.metadata or {}), "render_as": "text"}

    info = await check_for_update(force_refresh=True)
    if not info.checked_ok and info.error_kind != "no-acceptable-release":
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"Could not reach PyPI: {info.error_message or info.error_kind}",
            metadata=metadata,
        )
    if not info.update_available:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"Already on the latest version ({info.current}).",
            metadata=metadata,
        )

    cmd = upgrade_command(info.install_method)
    if cmd is None:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=(
                f"Update available: {info.latest} (you have {info.current}).\n"
                f"Auto-upgrade is not safe for {info.install_method.value} installs.\n"
                f"Run manually: {suggested_upgrade_command(info.install_method)}"
            ),
            metadata=metadata,
        )

    lock_path = get_update_dir() / ".lock"
    try:
        with FileLock(str(lock_path)).acquire(blocking=False):
            try:
                proc = await asyncio_mod.to_thread(
                    subprocess.run, cmd, capture_output=True, text=True, check=False
                )
            except FileNotFoundError:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=(
                        f"Could not find {cmd[0]} on PATH. "
                        f"Run manually: {suggested_upgrade_command(info.install_method)}"
                    ),
                    metadata=metadata,
                )
    except FileLockTimeout as e:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"Another upgrade is in progress (lock: {e.lock_file}).",
            metadata=metadata,
        )

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Upgrade failed:\n" + "\n".join(tail),
            metadata=metadata,
        )

    set_restart_notice_to_env(
        channel=msg.channel, chat_id=msg.chat_id, reason="upgrade"
    )

    async def _do_restart():
        await asyncio_mod.sleep(1)
        os_mod.execv(sys.executable, [sys.executable, "-m", "pythinker"] + sys.argv[1:])

    asyncio_mod.create_task(_do_restart())
    if ctx.loop is not None and hasattr(ctx.loop, "close_browser_session"):
        await ctx.loop.close_browser_session(ctx.key)
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=f"Upgraded {info.current} → {info.latest}. Restarting...",
        metadata=metadata,
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    try:
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    except Exception:
        pass
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)

    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    try:
        from pythinker.utils.searchusage import fetch_search_usage
        web_cfg = getattr(loop, "web_config", None)
        search_cfg = getattr(web_cfg, "search", None) if web_cfg else None
        if search_cfg is not None:
            provider = getattr(search_cfg, "provider", "duckduckgo")
            # Prefer the per-provider credential slot (current shape). Fall
            # back to the deprecated top-level api_key only when the migrated
            # accessor is not present on the cfg, so /status reports the same
            # state the runtime tool will actually use after onboarding /
            # auto-migration land the key under providers[<active>].
            credentials_for = getattr(search_cfg, "credentials_for", None)
            if callable(credentials_for):
                api_key = (credentials_for(provider).api_key or "") or None
            else:
                api_key = getattr(search_cfg, "api_key", "") or None
            usage = await fetch_search_usage(provider=provider, api_key=api_key)
            search_usage_text = usage.format()
    except Exception:
        pass  # Never let usage fetch break /status
    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    try:
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    except Exception:
        pass

    # Best-effort: surface a cached update status without ever hitting the
    # network from a chat command.
    update_status: str | None = None
    try:
        from pythinker.utils.update import _info_from_cache, _read_cache, format_banner

        cache = _read_cache()
        if cache:
            cached = _info_from_cache(cache)
            if cached is not None:
                line = format_banner(cached)
                if line:
                    update_status = line
    except Exception:
        update_status = None

    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
            max_completion_tokens=getattr(
                getattr(loop.provider, "generation", None), "max_tokens", 8192
            ),
            update_status=update_status,
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    await loop._cancel_active_tasks(ctx.key)
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.consolidator.archive(snapshot))
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        metadata=dict(ctx.msg.metadata or {})
    )


async def cmd_regenerate(ctx: CommandContext) -> None:
    """Drop the trailing assistant turn and re-run the prior user message.

    Routed as a priority command so the in-flight turn (if any) is cancelled
    and the per-session lock is awaited before mutating ``session.messages``.
    The agent loop is the single writer to session state — channels must not
    truncate directly. After mutation, a fresh :class:`InboundMessage` is
    published so the next agent iteration runs a new turn from that user
    message.
    """
    loop = ctx.loop
    msg = ctx.msg
    session_key = msg.session_key

    await loop._cancel_active_tasks(session_key)
    # Wait for the cancelled turn to finish releasing the per-session lock,
    # then perform the mutation under the same lock so we are the single
    # writer.
    lock = loop._session_locks.setdefault(session_key, asyncio.Lock())
    last_user_content: str | None = None
    async with lock:
        session = loop.sessions.get_or_create(session_key)
        user_indices = [
            i for i, m in enumerate(session.messages) if m.get("role") == "user"
        ]
        if not user_indices:
            return None
        last_user_idx = len(user_indices) - 1
        last_user_content = session.messages[user_indices[-1]].get("content", "")
        loop.sessions.truncate_after_user_index(
            session_key, user_msg_index=last_user_idx,
        )

    # Republish OUTSIDE the lock so the agent loop's own dispatch can acquire it.
    if last_user_content is None:
        return None
    await loop.bus.publish_inbound(
        InboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=last_user_content,
            sender_id=msg.sender_id,
            metadata={**dict(msg.metadata or {}), "regenerated": True},
        )
    )
    return None


async def cmd_edit(ctx: CommandContext) -> None:
    """Rewrite a user message in place and re-run from there.

    Same routing rationale as :func:`cmd_regenerate`: cancel the in-flight
    turn, await the per-session lock, then mutate + truncate + republish.
    Edit metadata is read from ``msg.metadata['edit_user_msg_index']`` and
    ``msg.metadata['edit_content']``; malformed input is a silent no-op
    (the channel pre-validates and surfaces user-facing errors).
    """
    loop = ctx.loop
    msg = ctx.msg
    session_key = msg.session_key
    md = msg.metadata or {}
    user_msg_index = md.get("edit_user_msg_index")
    new_content = md.get("edit_content")

    if not isinstance(user_msg_index, int) or not isinstance(new_content, str):
        return None
    if not new_content.strip():
        return None

    await loop._cancel_active_tasks(session_key)
    lock = loop._session_locks.setdefault(session_key, asyncio.Lock())
    mutated = False
    async with lock:
        session = loop.sessions.get_or_create(session_key)
        user_positions = [
            i for i, m in enumerate(session.messages) if m.get("role") == "user"
        ]
        if not (0 <= user_msg_index < len(user_positions)):
            return None
        session.messages[user_positions[user_msg_index]]["content"] = new_content
        loop.sessions.save(session)
        loop.sessions.truncate_after_user_index(
            session_key, user_msg_index=user_msg_index,
        )
        mutated = True

    if not mutated:
        return None
    # Strip the edit_* keys so the new turn's metadata is clean.
    forwarded_md = {
        k: v for k, v in md.items()
        if k not in ("edit_user_msg_index", "edit_content")
    }
    forwarded_md["edited"] = True
    await loop.bus.publish_inbound(
        InboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=new_content,
            sender_id=msg.sender_id,
            metadata=forwarded_md,
        )
    )
    return None


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels.

    Derives from :data:`BUILTIN_COMMAND_METADATA` so the ``/help`` text and the
    WebUI command palette never drift.
    """
    from pythinker.command.metadata import BUILTIN_COMMAND_METADATA

    lines = ["🐍 pythinker commands:"]
    for meta in BUILTIN_COMMAND_METADATA:
        lines.append(f"{meta.name} — {meta.summary}")
    return "\n".join(lines)
