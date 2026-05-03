"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from pythinker.agent.autocompact import AutoCompact
from pythinker.agent.context import ContextBuilder
from pythinker.agent.hook import AgentHook, AgentHookContext, CompositeHook
from pythinker.agent.memory import Consolidator, Dream
from pythinker.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from pythinker.agent.skills import BUILTIN_SKILLS_DIR
from pythinker.agent.subagent import SubagentManager
from pythinker.agent.tools.cron import CronTool
from pythinker.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from pythinker.agent.tools.message import MessageTool
from pythinker.agent.tools.notebook import NotebookEditTool
from pythinker.agent.tools.pdf import MakePdfTool
from pythinker.agent.tools.registry import ToolRegistry
from pythinker.agent.tools.search import GlobTool, GrepTool
from pythinker.agent.tools.self import MyTool
from pythinker.agent.tools.shell import ExecTool
from pythinker.agent.tools.spawn import SpawnTool
from pythinker.agent.tools.web import WebFetchTool, WebSearchTool
from pythinker.bus.events import InboundMessage, OutboundMessage
from pythinker.bus.queue import MessageBus
from pythinker.command import CommandContext, CommandRouter, register_builtin_commands
from pythinker.config.schema import AgentDefaults
from pythinker.providers.base import LLMProvider
from pythinker.runtime.egress import ToolEgressGateway
from pythinker.runtime.policy import PolicyService
from pythinker.session.manager import Session, SessionManager
from pythinker.utils.document import extract_documents
from pythinker.utils.helpers import image_placeholder_text
from pythinker.utils.helpers import truncate_text as truncate_text_fn
from pythinker.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

if TYPE_CHECKING:
    from pythinker.config.schema import (
        BrowserConfig,
        ChannelsConfig,
        ExecToolConfig,
        RuntimeConfig,
        ToolsConfig,
        WebToolsConfig,
    )
    from pythinker.cron.service import CronService
    from pythinker.providers.factory import ProviderSnapshot
    from pythinker.runtime.context import BudgetCounters, RequestContext


UNIFIED_SESSION_KEY = "unified:default"


@dataclasses.dataclass(slots=True)
class ToolEvent:
    name: str
    phase: str
    args_preview: str = ""
    result_preview: str = ""
    duration_ms: int | None = None


class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_tool_event: Callable[[ToolEvent], Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        session_key: str | None = None,
        request_context: "RequestContext | None" = None,
    ) -> None:
        super().__init__(reraise=True)
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._on_tool_event = on_tool_event
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._session_key = session_key
        self._request_context = request_context
        self._stream_buf = ""
        self._tools_started_at: float | None = None

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from pythinker.utils.helpers import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean) :]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._loop._current_iteration = context.iteration

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self._tools_started_at = time.monotonic()
        if self._on_progress:
            if not self._on_stream:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(
            self._channel, self._chat_id, self._message_id,
            session_key=self._session_key,
            request_context=self._request_context,
        )

    async def after_execute_tools(
        self,
        context: AgentHookContext,
        results: list[Any],
    ) -> None:
        if not self._on_tool_event:
            return
        duration_ms = None
        if self._tools_started_at is not None:
            duration_ms = int((time.monotonic() - self._tools_started_at) * 1000)
        for tool_call, event in zip(context.tool_calls, context.tool_events):
            args_preview = json.dumps(tool_call.arguments, ensure_ascii=False)[:80]
            await self._on_tool_event(ToolEvent(
                name=tool_call.name,
                phase="end" if event.get("status") == "ok" else "error",
                args_preview=args_preview,
                result_preview=(event.get("detail") or "")[:80],
                duration_ms=duration_ms,
            ))

    async def after_iteration(self, context: AgentHookContext) -> None:
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)


def _clamp_context_window(
    provider: LLMProvider, model: str, configured: int
) -> int:
    """Clamp ``configured`` to the provider's published input cap.

    Some plans publish hard limits the server enforces (e.g. ChatGPT/Codex
    OAuth caps gpt-5.5 input at 272k tokens). Without this, configured
    windows exceeding the cap drive silent server-side overflow after
    compaction has already trusted the larger budget.
    """
    limits = provider.get_model_limits(model)
    if not isinstance(limits, dict):
        return configured
    input_cap = limits.get("input")
    if not isinstance(input_cap, int) or input_cap <= 0:
        return configured
    if configured > input_cap:
        logger.info(
            "Clamping context_window_tokens {} → {} for model {} (provider cap)",
            configured, input_cap, model,
        )
        return input_cap
    return configured


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _PENDING_USER_TURN_KEY = "pending_user_turn"

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        web_config: WebToolsConfig | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        hooks: list[AgentHook] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        provider_snapshot_loader: Callable[[], "ProviderSnapshot"] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        browser_config_loader: Callable[[], "BrowserConfig"] | None = None,
        policy: "PolicyService | None" = None,
        runtime_config: "RuntimeConfig | None" = None,
        session_cache_max: int = 256,
    ):
        from pythinker.config.schema import (
            ExecToolConfig,
            RuntimeConfig,
            ToolsConfig,
            WebToolsConfig,
        )

        _tc = tools_config or ToolsConfig()
        self._runtime_config = runtime_config or RuntimeConfig()
        self._session_cache_max = session_cache_max  # consumed by Task 11 in SessionManager construction
        self.agent_registry = None  # Set later by Task 10b wiring (load_dir)
        # Source-of-truth for which manifest's allowed_tools are bound to inbound
        # requests. Operators set this via runtime.defaultAgentId. The value
        # is validated against the registry (Task 10b) — when the configured
        # id is missing, we log a warning and fall back to the first active
        # manifest, or to the literal "default" when the registry is empty.
        self.default_agent_id = self._runtime_config.default_agent_id
        defaults = AgentDefaults()
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        # Hot-reload plumbing: when set, _refresh_provider_snapshot is called
        # at the top of every _process_message and swaps provider/model in
        # place if the loader yields a different signature. Default None
        # preserves the legacy fixed-provider-for-life behaviour.
        self._provider_snapshot_loader: Callable[[], "ProviderSnapshot"] | None = (
            provider_snapshot_loader
        )
        self._provider_signature: tuple[object, ...] | None = provider_signature
        self._browser_config_loader: Callable[[], "BrowserConfig"] | None = (
            browser_config_loader
        )
        self._browser_signature: tuple[object, ...] | None = None
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        self.context_window_tokens = _clamp_context_window(
            self.provider,
            self.model,
            (
                context_window_tokens
                if context_window_tokens is not None
                else defaults.context_window_tokens
            ),
        )
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.web_config = web_config or WebToolsConfig()
        self._browser_signature = self.web_config.browser.signature()
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []

        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills)
        self.sessions = session_manager or SessionManager(
            workspace,
            cache_max=self._session_cache_max,
        )
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_config=self.web_config,
            max_tool_result_chars=self.max_tool_result_chars,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
            max_recursion_depth=self._runtime_config.max_subagent_recursion_depth,
        )
        self._unified_session = unified_session
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._browser_manager: Any = None
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue] = {}
        # PYTHINKER_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("PYTHINKER_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.consolidator = Consolidator(
            store=self.context.memory,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=self.context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=provider.generation.max_tokens,
        )
        self.auto_compact = AutoCompact(
            sessions=self.sessions,
            consolidator=self.consolidator,
            session_ttl_minutes=session_ttl_minutes,
        )
        self.dream = Dream(
            store=self.context.memory,
            provider=provider,
            model=self.model,
        )
        self._register_default_tools()
        if _tc.my.enable:
            self.tools.register(MyTool(loop=self, modify_allowed=_tc.my.allow_set))
        self.policy = policy or PolicyService(enabled=False)
        self.egress = ToolEgressGateway(registry=self.tools, policy=self.policy)
        self._runtime_vars: dict[str, Any] = {}
        self._current_iteration: int = 0
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)
        if self._runtime_config.manifests_dir:
            from pathlib import Path as _Path

            from pythinker.runtime.manifest import AgentRegistry as _Registry

            self.agent_registry = _Registry.load_dir(_Path(self._runtime_config.manifests_dir))
            if self.policy is not None and self.policy.enabled:
                self.policy.apply_registry(self.agent_registry)
                # Log only the *active* count: registry.ids() includes draft /
                # deprecated / retired manifests that apply_registry filters out.
                logger.info(
                    "Loaded {} active manifest(s) from {} into PolicyService ({} total in directory)",
                    len(self.policy.active_agent_ids()),
                    self._runtime_config.manifests_dir,
                    len(self.agent_registry.ids()),
                )

            # Validate default_agent_id against the registry. Without this
            # the manifest-driven path silently lands in deny-all when
            # `default_agent_id` doesn't match any manifest.
            active_ids = self.policy.active_agent_ids() if self.policy is not None else []
            if self.default_agent_id not in active_ids:
                if active_ids:
                    fallback = active_ids[0]
                    logger.warning(
                        "runtime.default_agent_id={!r} is not an active manifest; "
                        "falling back to {!r} (active ids: {})",
                        self.default_agent_id, fallback, active_ids,
                    )
                    self.default_agent_id = fallback
                elif (
                    self.policy is not None
                    and self.policy.enabled
                    and self._runtime_config.policy_migration_mode is None
                ):
                    logger.warning(
                        "runtime.default_agent_id={!r} not found and no active manifests; "
                        "every tool call will be denied. Add a manifest with id={!r} "
                        "or set runtime.policyMigrationMode='allow-all'.",
                        self.default_agent_id, self.default_agent_id,
                    )

    def _apply_provider_snapshot(self, snapshot: "ProviderSnapshot") -> None:
        """Swap model/provider for future turns without disturbing an active one.

        Cascade the new provider into the runner, subagent manager, consolidator,
        and dream so every component shares one provider object. Same-signature
        snapshots short-circuit early.
        """
        provider = snapshot.provider
        model = snapshot.model
        context_window_tokens = _clamp_context_window(
            provider, model, snapshot.context_window_tokens
        )
        if self.provider is provider and self.model == model:
            return
        old_model = self.model
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self.runner.provider = provider
        self.subagents.set_provider(provider, model)
        self.consolidator.set_provider(provider, model, context_window_tokens)
        self.dream.set_provider(provider, model)
        self._provider_signature = snapshot.signature
        logger.info("Runtime model switched for next turn: {} -> {}", old_model, model)

    def _refresh_provider_snapshot(self) -> None:
        """Pull the latest snapshot and apply it if the signature changed.

        Called at the top of every _process_message so config edits land at
        the next turn boundary. Errors during load are logged and swallowed
        — a temporarily-broken config must not crash an in-flight session.
        """
        if self._provider_snapshot_loader is None:
            return
        try:
            snapshot = self._provider_snapshot_loader()
        except Exception:
            logger.exception("Failed to refresh provider config")
            return
        if snapshot.signature == self._provider_signature:
            return
        self._apply_provider_snapshot(snapshot)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = (
            self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
        )
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(MakePdfTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy)
            )
            self.tools.register(WebFetchTool(proxy=self.web_config.proxy))
            self._register_browser_tool(self.web_config.browser)
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )

    def _browser_storage_dir(self, config: "BrowserConfig") -> Path:
        if config.storage_state_dir:
            return Path(config.storage_state_dir).expanduser()
        from pythinker.config.paths import get_browser_storage_dir

        return get_browser_storage_dir()

    def _register_browser_tool(self, config: "BrowserConfig") -> None:
        self.tools.unregister("browser")
        self._browser_manager = None
        if not self.web_config.enable or not config.enable:
            return

        import importlib.util

        if importlib.util.find_spec("playwright") is None:
            logger.warning(
                "browser tool requested but playwright is not installed; "
                "reinstall or upgrade pythinker-ai to restore the packaged browser dependency."
            )
            return

        from pythinker.agent.browser.manager import BrowserSessionManager
        from pythinker.agent.tools.browser import BrowserTool

        storage_dir = self._browser_storage_dir(config)
        storage_dir.mkdir(parents=True, exist_ok=True)
        self._browser_manager = BrowserSessionManager(config, storage_dir=storage_dir)
        self.tools.register(BrowserTool(self._browser_manager))

    async def _refresh_browser_config(self) -> None:
        if self._browser_config_loader is None:
            return
        try:
            config = self._browser_config_loader()
        except Exception:
            logger.exception("Failed to refresh browser config")
            return
        signature = config.signature()
        if signature == self._browser_signature:
            return
        old_manager = getattr(self, "_browser_manager", None)
        self.tools.unregister("browser")
        self._browser_manager = None
        if old_manager is not None:
            try:
                await asyncio.wait_for(old_manager.shutdown(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("browser hot-reload shutdown exceeded 10s deadline")
                await old_manager.shutdown(force=True)
        self.web_config.browser = config
        self._browser_signature = signature
        self._register_browser_tool(config)
        logger.info("browser: config changed; rebuilt browser tool registration")

    async def _evict_idle_browser(self) -> None:
        manager = getattr(self, "_browser_manager", None)
        if manager is None:
            return
        try:
            closed = await manager.evict_idle()
        except Exception:
            logger.exception("browser idle eviction failed")
            return
        if closed:
            logger.info("browser: closed {} idle context(s)", closed)

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from pythinker.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stacks = await connect_mcp_servers(self._mcp_servers, self.tools)
            if self._mcp_stacks:
                self._mcp_connected = True
            else:
                logger.warning("No MCP servers connected successfully (will retry next message)")
        except asyncio.CancelledError:
            logger.warning("MCP connection cancelled (will retry next message)")
            self._mcp_stacks.clear()
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            self._mcp_stacks.clear()
        finally:
            self._mcp_connecting = False

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        *,
        session_key: str | None = None,
        request_context: "RequestContext | None" = None,
    ) -> None:
        """Update context for all tools that need routing info.

        ``session_key`` (when supplied by callers) is the canonical effective_key
        and overrides the legacy ``f"{channel}:{chat_id}"`` derivation. Required
        for cron-triggered jobs (which use ``cron:{job.id}``) and for any future
        flow that uses ``InboundMessage.session_key_override``.
        """
        effective_key = (
            session_key
            if session_key is not None
            else (UNIFIED_SESSION_KEY if self._unified_session else f"{channel}:{chat_id}")
        )
        for name in ("message", "spawn", "cron", "my", "browser"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name in ("spawn", "browser"):
                        tool.set_context(channel, chat_id, effective_key=effective_key)
                    else:
                        tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))
        if (spawn_tool := self.tools.get("spawn")) is not None and hasattr(
            spawn_tool, "set_request_context"
        ):
            # Always overwrite. ContextVars persist across awaits within the
            # same Task, so leaving stale state from a previous turn would
            # let a later (less-privileged) turn inherit a prior turn's
            # parent_context / parent_egress. Setting both unconditionally —
            # to None when we have nothing — is the safe default.
            spawn_tool.set_request_context(request_context)
            spawn_tool.set_egress(self.egress if request_context is not None else None)

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from pythinker.utils.helpers import strip_think

        return strip_think(text) or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation."""
        from pythinker.utils.tool_hints import format_tool_hints

        return format_tool_hints(tool_calls)

    async def _dispatch_command_inline(
        self,
        msg: InboundMessage,
        key: str,
        raw: str,
        dispatch_fn: Callable[[CommandContext], Awaitable[OutboundMessage | None]],
    ) -> None:
        """Dispatch a command directly from the run() loop and publish the result."""
        ctx = CommandContext(msg=msg, session=None, key=key, raw=raw, loop=self)
        result = await dispatch_fn(ctx)
        if result:
            await self.bus.publish_outbound(result)
        else:
            logger.warning("Command '{}' matched but dispatch returned None", raw)

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*.

        Returns the total number of cancelled tasks + subagents.
        """
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    def _budget_template(self) -> "BudgetCounters":
        from pythinker.runtime.context import BudgetCounters

        rt = self._runtime_config
        return BudgetCounters(
            max_tool_calls=rt.max_tool_calls_per_turn,
            max_wall_clock_s=rt.max_wall_clock_s,
        )

    def _resolve_agent(self) -> tuple[str, int]:
        """Return (agent_id, policy_version) for a fresh context.

        Identity resolution must match policy mounting: an agent_id is only
        bound to a request if its manifest is BOTH present AND active. A
        draft / deprecated / retired manifest must NOT become ctx.agent_id —
        that would produce telemetry tagged with an agent that has no live
        allow-list, making audit logs lie.

        Defensive against partial wiring at three points in the build order:

          - Tests that bypass `__init__` via `AgentLoop.__new__(AgentLoop)`
            may not have set `self.policy` yet. We `getattr` it instead of
            accessing directly so those tests don't trip over an AttributeError.
          - Tasks 8 and 9 land before Task 10b adds `active_agent_ids` and
            `apply_registry` to PolicyService. During that window
            `getattr(policy, "active_agent_ids", None)` is `None`; we fall
            through to the policy_version-only path so context normalization
            doesn't crash mid-build.
          - Task 10b onward, we delegate to `policy.active_agent_ids()`,
            which is the source of truth for "which manifests are mounted",
            and we read `policy_version` from the live PolicyService so
            context identity and policy decisions always carry the same
            number.
        """
        policy = getattr(self, "policy", None)
        if policy is None:
            return "default", 0
        # Read the live policy_version FIRST so an enabled policy without a
        # bound registry still stamps the correct version on every context.
        # Returning version=0 when policy was active produced telemetry that
        # claimed "ungoverned" for traffic that was actually being authorised
        # — breaking the audit story.
        policy_version = getattr(policy, "policy_version", 0)
        if self.agent_registry is None:
            return "default", policy_version
        active_ids = getattr(policy, "active_agent_ids", None)
        if not callable(active_ids):
            # Pre-Task-10b: policy exists but the registry-mounting API
            # doesn't yet. Fall back to the literal "default" — registry
            # binding is a Task 10b feature, not a Task 8 one.
            return "default", policy_version
        active = active_ids()
        if self.default_agent_id in active:
            return self.default_agent_id, policy_version
        # default_agent_id is missing or inactive — fall back to "default"
        # at the live policy_version.
        return "default", policy_version

    def _normalize_context(
        self,
        *,
        seed: dict[str, str],
        session_key: str,
    ) -> "RequestContext":
        from pythinker.runtime.context import RequestContext

        agent_id, policy_version = self._resolve_agent()
        ctx = RequestContext.for_inbound(
            channel=seed["channel"],
            sender_id=seed["sender_id"],
            chat_id=seed["chat_id"],
            session_key=session_key,
            budgets=self._budget_template(),
        )
        return ctx.with_agent_id(agent_id, policy_version=policy_version)

    def _normalize_context_for_direct(
        self,
        *,
        session_key: str,
        channel: str = "api",
        sender_id: str = "api-client",
        chat_id: str = "default",
    ) -> "RequestContext":
        return self._normalize_context(
            seed={"channel": channel, "sender_id": sender_id, "chat_id": chat_id},
            session_key=session_key,
        )

    def _normalize_context_for_cron(
        self, *, job_id: str, session_key: str,
    ) -> "RequestContext":
        return self._normalize_context(
            seed={"channel": "cron", "sender_id": "system", "chat_id": job_id},
            session_key=session_key,
        )

    def _normalize_context_for_heartbeat(self, *, session_key: str) -> "RequestContext":
        return self._normalize_context(
            seed={"channel": "heartbeat", "sender_id": "system", "chat_id": "default"},
            session_key=session_key,
        )

    def _attach_context(self, msg: "InboundMessage") -> None:
        """Populate msg.context using msg.context_seed, with a synthesized
        fallback for legacy InboundMessage constructions that lack a seed.

        Idempotent: if msg.context is already populated (e.g. process_direct
        attached it before publishing the message to the bus), short-circuit
        instead of re-normalizing. Re-normalizing would pick up a fresh
        trace_id and break call-tree correlation across re-attach paths.
        """
        if msg.context is not None:
            return
        seed = msg.context_seed or {
            "channel": msg.channel,
            "sender_id": msg.sender_id,
            "chat_id": msg.chat_id,
        }
        msg.context = self._normalize_context(
            seed=seed,
            session_key=self._effective_session_key(msg),
        )

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_tool_event: Callable[[ToolEvent], Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        pending_queue: asyncio.Queue | None = None,
        msg: "InboundMessage | None" = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        ctx_for_run = msg.context if msg is not None else None
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_tool_event=on_tool_event,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            session_key=session.key if session is not None else None,
            request_context=ctx_for_run,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
        )

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Drain follow-up messages from the pending queue.

            When no messages are immediately available but sub-agents
            spawned in this dispatch are still running, blocks until at
            least one result arrives (or timeout).  This keeps the runner
            loop alive so subsequent sub-agent completions are consumed
            in-order rather than dispatched separately.
            """
            if pending_queue is None:
                return []

            def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                runtime_ctx = self.context._build_runtime_context(
                    pending_msg.channel,
                    pending_msg.chat_id,
                    self.context.timezone,
                )
                if isinstance(user_content, str):
                    merged: str | list[dict[str, Any]] = f"{runtime_ctx}\n\n{user_content}"
                else:
                    merged = [{"type": "text", "text": runtime_ctx}] + user_content
                return {"role": "user", "content": merged}

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    items.append(_to_user_message(pending_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            # Block if nothing drained but sub-agents spawned in this dispatch
            # are still running.  Keeps the runner loop alive so subsequent
            # completions are injected in-order rather than dispatched separately.
            if (not items
                    and session is not None
                    and self.subagents.get_running_count_by_session(session.key) > 0):
                # Use ``asyncio.timeout`` (3.11+) rather than ``wait_for`` —
                # the latter can leave the wrapped coroutine GC'd-but-unawaited
                # if cancellation lands during wrapping, generating spurious
                # ``RuntimeWarning: coroutine 'Queue.get' was never awaited``.
                try:
                    async with asyncio.timeout(300):
                        msg = await pending_queue.get()
                except TimeoutError:
                    logger.warning(
                        "Timeout waiting for sub-agent completion in session {}",
                        session.key,
                    )
                    return items
                items.append(_to_user_message(msg))
                while len(items) < limit:
                    try:
                        items.append(_to_user_message(pending_queue.get_nowait()))
                    except asyncio.QueueEmpty:
                        break

            return items

        # Per-chat model override (Phase 3 same-provider scope). Stored on
        # ``Session.metadata['model_override']`` by the WebSocket channel's
        # ``set_model`` envelope handler. When unset (or session is None for
        # cron / system flows) we fall back to the loop default.
        # Subagents continue to use ``self.model`` — overrides do NOT propagate
        # to spawned task agents (they run independently and may need the
        # primary model's capabilities).
        effective_model = self.model
        if session is not None:
            override = session.metadata.get("model_override")
            if isinstance(override, str) and override.strip():
                effective_model = override.strip()

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=effective_model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            retry_wait_callback=on_retry_wait,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
            egress=self.egress if ctx_for_run is not None else None,
            request_context=ctx_for_run,
        ))
        self._last_usage = result.usage
        if result.usage:
            try:
                from pythinker.agent.usage_ledger import record_turn_usage

                record_turn_usage(
                    workspace=self.workspace,
                    session_key=session.key if session is not None else None,
                    provider=getattr(self.provider, "name", type(self.provider).__name__),
                    model=effective_model,
                    usage=result.usage,
                )
            except Exception:
                logger.exception("Failed to record usage ledger row")
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            # Push final content through stream so streaming channels
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                self.auto_compact.check_expired(
                    self._schedule_background,
                    active_session_keys=self._pending_queues.keys(),
                )
                await self._evict_idle_browser()
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            self._attach_context(msg)
            # Ingress policy gate. authorize_ingress is a no-op when policy is
            # disabled. When enabled with blocked_senders, the message is dropped
            # with a logged reason — we do NOT publish an outbound rejection
            # because that would let an attacker probe whether a sender is
            # blocked. Drop silently and rely on telemetry.
            ingress = self.policy.authorize_ingress(msg.context)
            if not ingress.allowed:
                # Hash the sender so we don't write raw user-attributable ids
                # (Slack user ids, email addresses, etc.) into logs.
                from pythinker.runtime.telemetry import hash_identity
                logger.warning(
                    "ingress denied for {}/sender_hash={}: {}",
                    msg.context.channel,
                    hash_identity(msg.context.sender_id),
                    ingress.reason,
                )
                continue
            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                await self._dispatch_command_inline(
                    msg, msg.session_key, raw,
                    self.commands.dispatch_priority,
                )
                continue
            effective_key = self._effective_session_key(msg)
            # If this session already has an active pending queue (i.e. a task
            # is processing this session), route the message there for mid-turn
            # injection instead of creating a competing task.
            if effective_key in self._pending_queues:
                # Non-priority commands must not be queued for injection;
                # dispatch them directly (same pattern as priority commands).
                if self.commands.is_dispatchable_command(raw):
                    await self._dispatch_command_inline(
                        msg, effective_key, raw,
                        self.commands.dispatch,
                    )
                    continue
                pending_msg = msg
                if effective_key != msg.session_key:
                    pending_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                try:
                    self._pending_queues[effective_key].put_nowait(pending_msg)
                except asyncio.QueueFull:
                    logger.warning(
                        "Pending queue full for session {}, falling back to queued task",
                        effective_key,
                    )
                else:
                    logger.info(
                        "Routed follow-up message to pending queue for session {}",
                        effective_key,
                    )
                    continue
            # Compute the effective session key before dispatching
            # This ensures /stop command can find tasks correctly when unified session is enabled
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(effective_key, []).append(task)
            task.add_done_callback(
                lambda t, k=effective_key: self._active_tasks.get(k, [])
                and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k, [])
                else None
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        from pythinker.runtime.telemetry import emit

        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()

        # Register a pending queue so follow-up messages for this session are
        # routed here (mid-turn injection) instead of spawning a new task.
        pending = asyncio.Queue(maxsize=20)
        self._pending_queues[session_key] = pending

        ctx = getattr(msg, "context", None)
        try:
            t0 = time.monotonic()
            async with lock:
                t1 = time.monotonic()
                async with gate:
                    t2 = time.monotonic()
                    if ctx is not None:
                        emit("turn_started", ctx, {
                            "lock_wait_s": t1 - t0,
                            "concurrency_wait_s": t2 - t1,
                            "inbound_queue_depth": self.bus.inbound_size,
                            "outbound_queue_depth": self.bus.outbound_size,
                            "active_sessions": len(self._pending_queues),
                        })
                    try:
                        on_stream = on_stream_end = None
                        if msg.metadata.get("_wants_stream"):
                            # Split one answer into distinct stream segments.
                            stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                            stream_segment = 0

                            def _current_stream_id() -> str:
                                return f"{stream_base_id}:{stream_segment}"

                            async def on_stream(delta: str) -> None:
                                meta = dict(msg.metadata or {})
                                meta["_stream_delta"] = True
                                meta["_stream_id"] = _current_stream_id()
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=msg.channel, chat_id=msg.chat_id,
                                    content=delta,
                                    metadata=meta,
                                ))

                            async def on_stream_end(*, resuming: bool = False) -> None:
                                nonlocal stream_segment
                                meta = dict(msg.metadata or {})
                                meta["_stream_end"] = True
                                meta["_resuming"] = resuming
                                meta["_stream_id"] = _current_stream_id()
                                await self.bus.publish_outbound(OutboundMessage(
                                    channel=msg.channel, chat_id=msg.chat_id,
                                    content="",
                                    metadata=meta,
                                ))
                                stream_segment += 1

                        response = await self._process_message(
                            msg, on_stream=on_stream, on_stream_end=on_stream_end,
                            pending_queue=pending,
                        )
                        if response is not None:
                            await self.bus.publish_outbound(response)
                        elif msg.channel == "cli":
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="", metadata=msg.metadata or {},
                            ))
                    except asyncio.CancelledError:
                        logger.info("Task cancelled for session {}", session_key)
                        # Preserve partial context from the interrupted turn so
                        # the user does not lose tool results and assistant
                        # messages accumulated before /stop.  The checkpoint was
                        # already persisted to session metadata by
                        # _emit_checkpoint during tool execution; materializing
                        # it into session history now makes it visible in the
                        # next conversation turn.
                        try:
                            key = self._effective_session_key(msg)
                            session = self.sessions.get_or_create(key)
                            if self._restore_runtime_checkpoint(session):
                                self._clear_pending_user_turn(session)
                                self.sessions.save(session)
                                logger.info(
                                    "Restored partial context for cancelled session {}",
                                    key,
                                )
                        except Exception:
                            logger.debug(
                                "Could not restore checkpoint for cancelled session {}",
                                session_key,
                                exc_info=True,
                            )
                        raise
                    except Exception:
                        logger.exception("Error processing message for session {}", session_key)
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="Sorry, I encountered an error.",
                        ))
                    finally:
                        if ctx is not None:
                            emit("turn_finished", ctx, {
                                "duration_s": time.monotonic() - t2,
                            })
        finally:
            # Drain any messages still in the pending queue and re-publish
            # them to the bus so they are processed as fresh inbound messages
            # rather than silently lost.
            queue = self._pending_queues.pop(session_key, None)
            if queue is not None:
                leftover = 0
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self.bus.publish_inbound(item)
                    leftover += 1
                if leftover:
                    logger.info(
                        "Re-published {} leftover message(s) to bus for session {}",
                        leftover, session_key,
                    )

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        for name, stack in self._mcp_stacks.items():
            try:
                await stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
        self._mcp_stacks.clear()

    async def close_browser(self) -> None:
        """Save and close all BrowserContexts; disconnect CDP; stop Playwright."""
        manager = getattr(self, "_browser_manager", None)
        if manager is None:
            return
        try:
            await asyncio.wait_for(manager.shutdown(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("browser shutdown exceeded 10s deadline; force-closing")
            await manager.shutdown(force=True)

    async def close_browser_session(self, effective_key: str) -> None:
        """Close one chat's BrowserContext. No-op when [browser] is not installed."""
        manager = getattr(self, "_browser_manager", None)
        if manager is None:
            return
        await manager.close_session(effective_key)

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def _maybe_schedule_chat_title(
        self, session, user_text: str, assistant_text: str
    ) -> None:
        """Generate and persist a webui chat title once per session, async.

        Only fires for ``websocket:`` sessions (where the title is actually
        displayed) and only on the first turn (count of persisted user
        messages == 1). A sidecar file blocks repeats. Failures are silent —
        title generation is a sidebar nicety, never load-bearing.
        """
        try:
            key = getattr(session, "key", "") or ""
            if not key.startswith("websocket:"):
                return
            user_msg_count = sum(
                1 for m in (session.messages or []) if m.get("role") == "user"
            )
            if user_msg_count != 1:
                return
            if self.sessions.get_title(key):
                return
        except Exception:
            return  # never block the main flow on title bookkeeping

        async def _run() -> None:
            from pythinker.agent.chat_title import generate_title

            title = await generate_title(self.provider, user_text, assistant_text)
            if title:
                self.sessions.set_title(key, title)

        self._schedule_background(_run())

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_tool_event: Callable[[ToolEvent], Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # Hot-reload check at the turn boundary so model/provider/api_key
        # edits in ~/.pythinker/config.json land without restarting. No-op
        # when the loader is None or the signature matches.
        self._refresh_provider_snapshot()
        await self._refresh_browser_config()
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            if self._restore_runtime_checkpoint(session):
                self.sessions.save(session)
            if self._restore_pending_user_turn(session):
                self.sessions.save(session)

            session, pending = self.auto_compact.prepare_session(session, key)

            await self.consolidator.maybe_consolidate_by_tokens(
                session,
                session_summary=pending,
            )
            # Persist subagent follow-ups into durable history BEFORE prompt
            # assembly. ContextBuilder merges adjacent same-role messages for
            # provider compatibility, which previously caused the follow-up to
            # disappear from session.messages while still being visible to the
            # LLM via the merged prompt. See _persist_subagent_followup.
            is_subagent = msg.sender_id == "subagent"
            if is_subagent and self._persist_subagent_followup(session, msg):
                self.sessions.save(session)
            self._set_tool_context(
                channel, chat_id, msg.metadata.get("message_id"), session_key=session.key,
                request_context=msg.context,
            )
            history = session.get_history(max_messages=0)
            current_role = "assistant" if is_subagent else "user"

            # Subagent content is already in `history` above; passing it again
            # as current_message would double-project it into the prompt.
            messages = self.context.build_messages(
                history=history,
                current_message="" if is_subagent else msg.content,
                channel=channel,
                chat_id=chat_id,
                session_summary=pending,
                current_role=current_role,
            )
            final_content, _, all_msgs, _, _ = await self._run_agent_loop(
                messages, session=session, channel=channel, chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
                pending_queue=pending_queue,
                msg=msg,
            )
            self._save_turn(session, all_msgs, 1 + len(history))
            self._clear_runtime_checkpoint(session)
            self.sessions.save(session)
            self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
            )

        # Extract document text from media at the processing boundary so all
        # channels benefit without format-specific logic in ContextBuilder.
        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            msg = dataclasses.replace(msg, content=new_content, media=image_only)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session)
        if self._restore_pending_user_turn(session):
            self.sessions.save(session)

        session, pending = self.auto_compact.prepare_session(session, key)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self.consolidator.maybe_consolidate_by_tokens(
            session,
            session_summary=pending,
        )

        self._set_tool_context(
            msg.channel, msg.chat_id, msg.metadata.get("message_id"),
            session_key=session.key if session is not None else None,
            request_context=msg.context,
        )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)

        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            session_summary=pending,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        async def _on_retry_wait(content: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_retry_wait"] = True
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        # Persist the triggering user message up front so a mid-turn crash
        # doesn't silently lose the prompt on recovery. ``media`` rides along
        # as raw on-disk paths — sanitized image blocks are stripped from
        # JSONL, and webui replay needs the paths to mint signed URLs.
        user_persisted_early = False
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if has_text or media_paths:
            extra: dict[str, Any] = {"media": list(media_paths)} if media_paths else {}
            text = msg.content if isinstance(msg.content, str) else ""
            session.add_message("user", text, **extra)
            self._mark_pending_user_turn(session)
            self.sessions.save(session)
            user_persisted_early = True

        final_content, _, all_msgs, stop_reason, had_injections = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_tool_event=on_tool_event,
            on_retry_wait=_on_retry_wait,
            session=session,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            pending_queue=pending_queue,
            msg=msg,
        )

        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Skip the already-persisted user message when saving the turn
        save_skip = 1 + len(history) + (1 if user_persisted_early else 0)
        self._save_turn(session, all_msgs, save_skip)
        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))
        # Best-effort: name freshly-created webui chats from the first turn so
        # the sidebar shows something meaningful instead of "New chat".
        self._maybe_schedule_chat_title(session, msg.content or "", final_content)

        # When follow-up messages were injected mid-turn, a later natural
        # language reply may address those follow-ups and should not be
        # suppressed just because MessageTool was used earlier in the turn.
        # However, if the turn falls back to the empty-final-response
        # placeholder, suppress it when the real user-visible output already
        # came from MessageTool.
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None and stop_reason != "error":
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the entire runtime-context block (including any session summary).
                    # The block is bounded by _RUNTIME_CONTEXT_TAG and _RUNTIME_CONTEXT_END.
                    end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                    end_pos = content.find(end_marker)
                    if end_pos >= 0:
                        after = content[end_pos + len(end_marker):].lstrip("\n")
                        if after:
                            entry["content"] = after
                        else:
                            continue
                    else:
                        # Fallback: no end marker found, strip the tag prefix
                        after_tag = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG):].lstrip("\n")
                        if after_tag.strip():
                            entry["content"] = after_tag
                        else:
                            continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        from datetime import datetime

        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self._clear_pending_user_turn(session)
        return True

    def preauthorize_direct(
        self,
        *,
        session_key: str,
        channel: str = "api",
        sender_id: str = "api-client",
        chat_id: str = "default",
    ) -> "RequestContext":
        """Run only the ingress-policy step for a would-be process_direct call.

        Used by the streaming API server to convert ingress denial into a
        403 response BEFORE writing 200 headers. Returns the normalized
        RequestContext on allow so the caller can pass it back into
        process_direct(..., request_context=ctx) and avoid a second
        normalization + ingress check (which would mint a fresh trace_id
        and double-emit policy_decision).

        Raises PermissionError on denial.
        """
        ctx = self._normalize_context_for_direct(
            session_key=session_key, channel=channel,
            sender_id=sender_id, chat_id=chat_id,
        )
        decision = self.policy.authorize_ingress(ctx)
        if not decision.allowed:
            raise PermissionError(f"ingress denied: {decision.reason}")
        return ctx

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "api",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_tool_event: Callable[[ToolEvent], Awaitable[None]] | None = None,
        *,
        sender_id: str = "api-client",
        request_context: "RequestContext | None" = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload.

        Keyword args:
            sender_id: Identity stamped on the synthesized RequestContext.
                Used for `authorize_ingress` blocked-sender lookups.
            request_context: When provided, the caller has already
                pre-authorized (typical for the streaming API path: see
                `preauthorize_direct` introduced by Task 8c). The same
                context is reused so trace_id stays stable across the
                pre-auth and run.

        Raises:
            PermissionError: When ingress policy denies the synthesized
                context (Task 8c maps this to HTTP 403 in the API).
        """
        if request_context is not None:
            # Caller already pre-authorized (streaming API path: see
            # preauthorize_direct). Reuse the same context — running
            # _normalize_context_for_direct again would mint a fresh
            # trace_id and double-emit ingress policy telemetry for one
            # logical request.
            ctx = request_context
        else:
            ctx = self._normalize_context_for_direct(
                session_key=session_key,
                channel=channel,
                sender_id=sender_id,
                chat_id=chat_id,
            )
            ingress = self.policy.authorize_ingress(ctx)
            if not ingress.allowed:
                raise PermissionError(f"ingress denied: {ingress.reason}")
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id=sender_id, chat_id=chat_id,
            content=content, media=media or [],
            context=ctx,
            context_seed={"channel": channel, "sender_id": sender_id, "chat_id": chat_id},
        )
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_tool_event=on_tool_event,
        )
