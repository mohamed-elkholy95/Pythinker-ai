"""Spawn tool for creating background subagents."""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from pythinker.agent.tools.base import Tool, tool_parameters
from pythinker.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from pythinker.agent.runner import EgressGateway
    from pythinker.agent.subagent import SubagentManager
    from pythinker.runtime.context import RequestContext


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        required=["task"],
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel: ContextVar[str] = ContextVar("spawn_origin_channel", default="cli")
        self._origin_chat_id: ContextVar[str] = ContextVar("spawn_origin_chat_id", default="direct")
        self._session_key: ContextVar[str] = ContextVar("spawn_session_key", default="cli:direct")
        self._parent_context: "ContextVar[RequestContext | None]" = ContextVar(
            "spawn_parent_context", default=None,
        )
        self._parent_egress: "ContextVar[EgressGateway | None]" = ContextVar(
            "spawn_parent_egress", default=None,
        )

    def set_context(self, channel: str, chat_id: str, effective_key: str | None = None) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel.set(channel)
        self._origin_chat_id.set(chat_id)
        self._session_key.set(effective_key or f"{channel}:{chat_id}")

    def set_request_context(self, ctx: "RequestContext | None") -> None:
        """Set the parent RequestContext for egress inheritance."""
        self._parent_context.set(ctx)

    def set_egress(self, egress: "EgressGateway | None") -> None:
        """Set the parent EgressGateway forwarded to the spawned subagent."""
        self._parent_egress.set(egress)

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str | None:
        """Spawn a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel.get(),
            origin_chat_id=self._origin_chat_id.get(),
            session_key=self._session_key.get(),
            parent_context=self._parent_context.get(),
            parent_egress=self._parent_egress.get(),
        )
