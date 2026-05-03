"""High-level programmatic interface to pythinker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pythinker.agent.hook import AgentHook
from pythinker.agent.loop import AgentLoop
from pythinker.bus.queue import MessageBus


@dataclass(slots=True)
class RunResult:
    """Result of a single agent run."""

    content: str
    tools_used: list[str]
    messages: list[dict[str, Any]]


class Pythinker:
    """Programmatic facade for running the pythinker agent.

    Usage::

        bot = Pythinker.from_config()
        result = await bot.run("Summarize this repo", hooks=[MyHook()])
        print(result.content)
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> Pythinker:
        """Create a Pythinker instance from a config file.

        Args:
            config_path: Path to ``config.json``.  Defaults to
                ``~/.pythinker/config.json``.
            workspace: Override the workspace directory from config.
        """
        from pythinker.config.loader import load_config, resolve_config_env_vars
        from pythinker.config.schema import Config

        resolved: Path | None = None
        if config_path is not None:
            resolved = Path(config_path).expanduser().resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Config not found: {resolved}")

        config: Config = resolve_config_env_vars(load_config(resolved))
        if workspace is not None:
            config.agents.defaults.workspace = str(
                Path(workspace).expanduser().resolve()
            )

        from pythinker.providers.factory import (
            build_provider_snapshot,
            load_provider_snapshot,
            provider_signature,
        )

        snapshot = build_provider_snapshot(config)
        bus = MessageBus()
        defaults = config.agents.defaults

        # Hot-reload loader: rebuild the snapshot from disk on each turn so
        # `~/.pythinker/config.json` edits to model/provider/api_key land at
        # the next turn boundary without re-instantiating the SDK.
        snapshot_loader = lambda: load_provider_snapshot(resolved)  # noqa: E731

        def browser_config_loader():
            latest = resolve_config_env_vars(load_config(resolved))
            if workspace is not None:
                latest.agents.defaults.workspace = str(
                    Path(workspace).expanduser().resolve()
                )
            return latest.tools.web.browser

        from pythinker.runtime._bootstrap import build_policy, install_telemetry_sink

        install_telemetry_sink(config)
        policy = build_policy(config)

        loop = AgentLoop(
            bus=bus,
            provider=snapshot.provider,
            workspace=config.workspace_path,
            model=defaults.model,
            max_iterations=defaults.max_tool_iterations,
            context_window_tokens=defaults.context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            provider_retry_mode=defaults.provider_retry_mode,
            web_config=config.tools.web,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            timezone=defaults.timezone,
            unified_session=defaults.unified_session,
            disabled_skills=defaults.disabled_skills,
            session_ttl_minutes=defaults.session_ttl_minutes,
            tools_config=config.tools,
            provider_snapshot_loader=snapshot_loader,
            provider_signature=provider_signature(config),
            browser_config_loader=browser_config_loader,
            runtime_config=config.runtime,
            policy=policy,
            session_cache_max=config.runtime.session_cache_max,
        )
        return cls(loop)

    async def run(
        self,
        message: str,
        *,
        session_key: str = "sdk:default",
        hooks: list[AgentHook] | None = None,
    ) -> RunResult:
        """Run the agent once and return the result.

        Args:
            message: The user message to process.
            session_key: Session identifier for conversation isolation.
                Different keys get independent history.
            hooks: Optional lifecycle hooks for this run.
        """
        prev = self._loop._extra_hooks
        if hooks is not None:
            self._loop._extra_hooks = list(hooks)
        try:
            # Tag SDK calls so telemetry / policy / sender-blocklists treat
            # them as SDK traffic rather than mislabelled "api" traffic.
            # process_direct's defaults (channel="api", sender_id="api-client")
            # are correct for the OpenAI-compatible HTTP server, not for the
            # in-process facade.
            response = await self._loop.process_direct(
                message,
                session_key=session_key,
                channel="sdk",
                chat_id="sdk",
                sender_id="sdk-client",
            )
        finally:
            self._loop._extra_hooks = prev

        content = (response.content if response else None) or ""
        return RunResult(content=content, tools_used=[], messages=[])


def _make_provider(config: Any) -> Any:
    """Create the LLM provider from config.

    Thin SDK-side wrapper around `providers.factory.make_provider` so a
    single function determines provider identity for the SDK, the CLI, and
    `serve` / `gateway`. ValueError from validation propagates to the
    caller.
    """
    from pythinker.providers.factory import make_provider

    return make_provider(config)
