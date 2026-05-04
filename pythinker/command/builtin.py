"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import sys

from pythinker import __version__
from pythinker.bus.events import InboundMessage, OutboundMessage
from pythinker.command.router import CommandContext, CommandRouter
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


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv."""
    msg = ctx.msg
    set_restart_notice_to_env(channel=msg.channel, chat_id=msg.chat_id)

    async def _do_restart():
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "pythinker"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
    if ctx.loop is not None and hasattr(ctx.loop, "close_browser_session"):
        await ctx.loop.close_browser_session(ctx.key)
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_upgrade(ctx: CommandContext) -> OutboundMessage:
    """Check PyPI and upgrade pythinker in place; restart on success."""
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
                proc = await asyncio.to_thread(
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
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "pythinker"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
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


def _task_id_from_args(args: str) -> str:
    """Return the first whitespace-delimited task id argument."""
    parts = args.strip().split(maxsplit=1)
    return parts[0] if parts else ""


def _escape_markdown_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _task_record_for_session(ctx: CommandContext, task_id: str):
    record = ctx.loop.task_store.get(task_id)
    if record is None or record.session_key != ctx.key or record.status not in {"pending", "running"}:
        return None
    return record


def _task_output_record_for_session(ctx: CommandContext, task_id: str):
    record = ctx.loop.task_store.get(task_id)
    if record is None or record.session_key != ctx.key:
        return None
    return record


def _fenced_text(content: str) -> str:
    safe_content = content.replace("```", "`\\`\\`")
    return f"```text\n{safe_content}\n```"


def _format_task_row(record) -> str:
    return (
        f"- `{record.task_id}` {record.status} - {_escape_markdown_text(record.label)} "
        f"(updated {record.updated_at})"
    )


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


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    # Build a system_dream-bound context so the manual /dream path is
    # governed by the same egress gateway and policy exemption as the cron
    # path. Without this, Dream's tool calls would bypass the gateway —
    # violating the runtime invariant "no traffic bypasses controls".
    rctx = loop._normalize_context_for_cron(job_id="dream", session_key="cron:dream")
    rctx = rctx.with_agent_id("system_dream", policy_version=loop.policy.policy_version)

    async def _run_dream():
        t0 = time.monotonic()
        try:
            did_work = await loop.dream.run(request_context=rctx, egress=loop.egress)
            elapsed = time.monotonic() - t0
            if did_work:
                content = f"Dream completed in {elapsed:.1f}s."
            else:
                content = "Dream: nothing to process."
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


def _format_dream_log_content(commit, diff: str, *, requested_sha: str | None = None) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.message.splitlines()[0]}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest commit (HEAD~1 vs HEAD).
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle."
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest commit's diff
        commits = git.log(max_entries=1)
        result = git.show_commit_diff(commits[0].sha) if commits else None
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = "Dream memory has no saved versions yet."

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent commits for the user to pick
        commits = git.log(max_entries=10)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        changed_files = _format_changed_files(result[1]) if result else "the tracked memory files"
        new_sha = git.revert(sha)
        if new_sha:
            content = (
                f"Restored Dream memory to the state before `{sha}`.\n\n"
                f"- New safety commit: `{new_sha}`\n"
                f"- Restored files: {changed_files}\n\n"
                f"Use `/dream-log {new_sha}` to inspect the restore diff."
            )
        else:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "It may not exist, or it may be the first saved version with no earlier state to restore."
            )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
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


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.priority("/regenerate", cmd_regenerate)
    router.priority("/edit", cmd_edit)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/tasks", cmd_tasks)
    router.exact("/task-output", cmd_task_output)
    router.prefix("/task-output ", cmd_task_output)
    router.exact("/task-stop", cmd_task_stop)
    router.prefix("/task-stop ", cmd_task_stop)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/help", cmd_help)
    router.exact("/upgrade", cmd_upgrade)
