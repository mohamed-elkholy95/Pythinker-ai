"""Subagent manager for background task execution."""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from pythinker.agent.hook import AgentHook, AgentHookContext
from pythinker.agent.runner import AgentRunner, AgentRunSpec
from pythinker.agent.skills import BUILTIN_SKILLS_DIR
from pythinker.agent.task_store import TaskStore
from pythinker.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from pythinker.agent.tools.registry import ToolRegistry
from pythinker.agent.tools.search import GlobTool, GrepTool
from pythinker.agent.tools.shell import ExecTool
from pythinker.agent.tools.web import WebFetchTool, WebSearchTool
from pythinker.bus.events import InboundMessage
from pythinker.bus.queue import MessageBus
from pythinker.config.schema import ExecToolConfig, WebToolsConfig
from pythinker.providers.base import LLMProvider
from pythinker.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from pythinker.agent.runner import EgressGateway
    from pythinker.runtime.context import RequestContext


_TASK_STORE_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "orphaned"}


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic() — for elapsed_s only
    started_at_wall: float = 0.0  # time.time() at spawn — for absolute UI display
    started_at_iso: str = ""      # ISO-8601 of started_at_wall
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(
        self,
        task_id: str,
        status: SubagentStatus | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._task_store = task_store

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is not None:
            self._status.iteration = context.iteration
            self._status.tool_events = list(context.tool_events)
            self._status.usage = dict(context.usage)
            if context.error:
                self._status.error = str(context.error)
        if self._task_store is not None:
            self._task_store.update_task(
                self._task_id,
                recent_activity=_tool_activity(context.tool_events) or None,
                usage=dict(context.usage),
                error=str(context.error) if context.error else None,
            )


def _tool_activity(tool_events: list[dict[str, str]]) -> list[dict[str, str]]:
    activities: list[dict[str, str]] = []
    for event in tool_events:
        if not event.get("name"):
            continue
        activities.append({str(k): str(v) for k, v in event.items() if v is not None})
    return activities


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        web_config: "WebToolsConfig | None" = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        max_recursion_depth: int = 3,
        task_store: TaskStore | None = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.web_config = web_config or WebToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self._max_recursion_depth = max_recursion_depth
        self.runner = AgentRunner(provider)
        self.task_store = task_store or TaskStore(workspace)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        """Hot-swap provider+model. Called from AgentLoop._apply_provider_snapshot
        when a config edit changes the active LLM identity."""
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        parent_context: "RequestContext | None" = None,
        parent_egress: "EgressGateway | None" = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background.

        Note:
            parent_context and parent_egress must be set together — passing one
            without the other will raise ValueError inside the spawned task
            (Task 7 XOR guard on AgentRunSpec). Production wiring in
            AgentLoop._set_tool_context sets both atomically; tests calling
            spawn() directly should follow the same convention.
        """
        if parent_context is not None:
            would_be_depth = parent_context.recursion_depth + 1
            if would_be_depth > self._max_recursion_depth:
                logger.warning(
                    "subagent spawn rejected: depth {} would exceed limit {}",
                    would_be_depth, self._max_recursion_depth,
                )
                return (
                    f"Spawn rejected: subagent recursion depth "
                    f"{would_be_depth} would exceed limit {self._max_recursion_depth}."
                )

        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        task_session_key = session_key or f"{origin_channel}:{origin_chat_id}"
        task_record = self.task_store.start_task(
            task_type="subagent",
            label=display_label,
            description=task,
            session_key=task_session_key,
        )
        task_id = task_record.task_id
        # Capture parent identity so the late-completion announce can rebuild
        # a governed context with the parent's channel/sender_id/chat_id
        # (and resolved agent_id) rather than falling back to system/subagent
        # /default. Without this, subagent completions arriving after the
        # parent turn ends ran with "ungoverned" identity in telemetry.
        parent_sender_id = (
            parent_context.sender_id if parent_context is not None else "system"
        )
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
            "session_key": task_session_key,
            "sender_id": parent_sender_id,
        }

        wall_now = time.time()
        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
            started_at_wall=wall_now,
            started_at_iso=datetime.fromtimestamp(wall_now, tz=timezone.utc).isoformat(),
        )
        self._task_statuses[task_id] = status

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id, task, display_label, origin, status,
                parent_context=parent_context,
                parent_egress=parent_egress,
            )
        )
        self._running_tasks[task_id] = bg_task
        if task_session_key:
            self._session_tasks.setdefault(task_session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if task_session_key and (ids := self._session_tasks.get(task_session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[task_session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus,
        *,
        parent_context: "RequestContext | None" = None,
        parent_egress: "EgressGateway | None" = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
            extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(GlobTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(GrepTool(workspace=self.workspace, allowed_dir=allowed_dir))
            if self.exec_config.enable:
                tools.register(ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                ))
            if self.web_config.enable:
                tools.register(WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy))
                tools.register(WebFetchTool(proxy=self.web_config.proxy))
            system_prompt = self._build_subagent_prompt()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            from pythinker.runtime.context import RequestContext

            child_ctx: RequestContext | None = None
            if parent_context is not None:
                child_ctx = parent_context.child_for_subagent(label=label)

            result = await self.runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=15,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=_SubagentHook(task_id, status, self.task_store),
                max_iterations_message="Task completed but no final response was generated.",
                error_message=None,
                fail_on_tool_error=True,
                checkpoint_callback=_on_checkpoint,
                request_context=child_ctx,
                egress=parent_egress if child_ctx is not None else None,
            ))
            status.phase = "done"
            status.stop_reason = result.stop_reason
            status.usage = dict(getattr(result, "usage", {}) or {})

            usage = getattr(result, "usage", None)
            self.task_store.update_task(
                task_id,
                recent_activity=_tool_activity(getattr(result, "tool_events", []) or []) or None,
                usage=dict(usage) if usage is not None else None,
                stop_reason=result.stop_reason,
                error=result.error,
            )

            if result.stop_reason == "tool_error":
                status.tool_events = list(result.tool_events)
                output = self._format_partial_progress(result)
                self.task_store.append_output(task_id, output)
                self.task_store.finish_task(
                    task_id,
                    status="failed",
                    stop_reason=result.stop_reason,
                    error=result.error,
                )
                await self._announce_result(
                    task_id, label, task,
                    output,
                    origin, "error",
                )
            elif result.stop_reason == "error":
                output = result.error or "Error: subagent execution failed."
                self.task_store.append_output(task_id, output)
                self.task_store.finish_task(
                    task_id,
                    status="failed",
                    stop_reason=result.stop_reason,
                    error=result.error,
                )
                await self._announce_result(
                    task_id, label, task,
                    output,
                    origin, "error",
                )
            elif result.error:
                output = result.error
                self.task_store.append_output(task_id, output)
                self.task_store.finish_task(
                    task_id,
                    status="failed",
                    stop_reason=result.stop_reason,
                    error=result.error,
                )
                await self._announce_result(
                    task_id, label, task,
                    output,
                    origin, "error",
                )
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
                logger.info("Subagent [{}] completed successfully", task_id)
                self.task_store.append_output(task_id, final_result)
                self.task_store.finish_task(
                    task_id,
                    status="completed",
                    stop_reason=result.stop_reason,
                    error=result.error,
                )
                await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.error("Subagent [{}] failed: {}", task_id, e)
            output = f"Error: {e}"
            self.task_store.append_output(task_id, output)
            self.task_store.finish_task(task_id, status="failed", error=str(e))
            await self._announce_result(task_id, label, task, output, origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        # Carry the parent's identity in context_seed so AgentLoop._attach_context
        # rebuilds a governed RequestContext (with the parent's channel/
        # sender_id/chat_id and the loop's resolved agent_id + live
        # policy_version) for late completions, instead of synthesising a
        # "system/subagent/default" context that bypasses audit identity.
        # The InboundMessage's outer channel/sender_id stay as system/subagent
        # so existing routing behaviour (and the injected_event metadata flag)
        # is preserved.
        parent_seed = {
            "channel": origin["channel"],
            "sender_id": origin.get("sender_id", "system"),
            "chat_id": origin["chat_id"],
        }
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            context_seed=parent_seed,
            metadata={
                "injected_event": "subagent_result",
                "subagent_task_id": task_id,
            },
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from pythinker.agent.context import ContextBuilder
        from pythinker.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(self.workspace),
            skills_summary=skills_summary or "",
        )

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel one subagent by ``task_id``. Returns True if cancelled.

        Returns False if the task is unknown or already done. Cleanup of
        ``_task_statuses`` / ``_session_tasks`` is handled by the existing
        done-callback registered in :meth:`spawn`.
        """
        task = self._running_tasks.get(task_id)
        if task is None or task.done():
            return False
        record = self.task_store.get(task_id)
        if record is not None and record.status in _TASK_STORE_TERMINAL_STATUSES:
            return False
        task.cancel()
        self.task_store.cancel_task(task_id)
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        task_ids = [
            tid for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
            and not (
                (record := self.task_store.get(tid)) is not None
                and record.status in _TASK_STORE_TERMINAL_STATUSES
            )
        ]
        tasks = [self._running_tasks[tid] for tid in task_ids]
        for t in tasks:
            t.cancel()
        for tid in task_ids:
            self.task_store.cancel_task(tid)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def list_statuses(self) -> list[dict[str, Any]]:
        """JSON-safe snapshot of all live subagent statuses.

        Each row carries ``elapsed_s`` (computed from the monotonic clock) and
        ``session_key`` (resolved from ``_session_tasks``). The internal
        monotonic ``started_at`` is dropped — callers should display
        ``started_at_iso``.
        """
        task_to_session: dict[str, str] = {}
        for skey, tids in self._session_tasks.items():
            for tid in tids:
                task_to_session[tid] = skey

        now = time.monotonic()
        rows: list[dict[str, Any]] = []
        for tid, status in self._task_statuses.items():
            row = asdict(status)
            row.pop("started_at", None)
            row["elapsed_s"] = max(0.0, now - status.started_at)
            row["session_key"] = task_to_session.get(tid, "")
            rows.append(row)
        return rows
