"""Slash command registry and dispatch.

Lines that start with ``/`` and have nothing before the slash are routed
through this module. Everything else goes to the agent verbatim.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


@dataclass(frozen=True)
class SlashCommand:
    name: str
    aliases: tuple[str, ...]
    summary: str
    handler: Callable[["TuiApp", list[str]], Awaitable[None]]


async def _cmd_help(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.screens.help import HelpScreen
    app.overlay.push(HelpScreen(SLASH_COMMANDS))


async def _cmd_exit(app: "TuiApp", args: list[str]) -> None:
    app.application.exit()


async def _cmd_clear(app: "TuiApp", args: list[str]) -> None:
    app.chat_pane.clear()


async def _cmd_new(app: "TuiApp", args: list[str]) -> None:
    from uuid import uuid4
    app.state.session_key = f"cli:tui:{uuid4().hex[:8]}"
    app.chat_pane.clear()
    app.status_bar.refresh()


async def _cmd_sessions(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.pickers.sessions import open_sessions_picker
    await open_sessions_picker(app)


async def _cmd_model(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.pickers.model import open_model_picker
    await open_model_picker(app)


async def _cmd_provider(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.pickers.provider import open_provider_picker
    await open_provider_picker(app)


async def _cmd_theme(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.pickers.theme import open_theme_picker
    await open_theme_picker(app)


async def _cmd_status(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.screens.status import StatusScreen
    app.overlay.push(StatusScreen(app))


async def _reset_mcp_runtime(agent_loop: object) -> None:
    close_mcp = getattr(agent_loop, "close_mcp", None)
    if callable(close_mcp):
        result = close_mcp()
        if isawaitable(result):
            await result
    else:
        stacks = getattr(agent_loop, "_mcp_stacks", {}) or {}
        for stack in list(stacks.values()):
            aclose = getattr(stack, "aclose", None)
            if callable(aclose):
                result = aclose()
                if isawaitable(result):
                    await result
        stacks.clear()

    tools = getattr(agent_loop, "tools", None)
    unregister = getattr(tools, "unregister", None)
    if callable(unregister):
        for name in list(getattr(tools, "tool_names", [])):
            if name.startswith("mcp_"):
                unregister(name)

    setattr(agent_loop, "_mcp_stacks", {})
    setattr(agent_loop, "_mcp_connected", False)
    setattr(agent_loop, "_mcp_connecting", False)


async def _sync_mcp_config(app: "TuiApp") -> None:
    """Refresh MCP server config from disk before showing/reconnecting status."""
    from pythinker.config.loader import load_config, resolve_config_env_vars

    opts = getattr(app, "options", None)
    raw_config_path = getattr(opts, "config_path", None)
    config_path = Path(raw_config_path).expanduser().resolve() if raw_config_path else None

    fresh_config = resolve_config_env_vars(load_config(config_path))
    workspace = getattr(opts, "workspace", None)
    if workspace:
        fresh_config.agents.defaults.workspace = workspace

    app.config = fresh_config
    fresh_servers = fresh_config.tools.mcp_servers
    agent_loop = app.agent_loop
    if getattr(agent_loop, "_mcp_servers", {}) == fresh_servers:
        return

    await _reset_mcp_runtime(agent_loop)
    agent_loop._mcp_servers = fresh_servers  # noqa: SLF001


async def _cmd_mcp(app: "TuiApp", args: list[str]) -> None:
    from pythinker.cli.tui.screens.mcp import McpScreen

    try:
        await _sync_mcp_config(app)
    except Exception:
        app.chat_pane.append_notice("MCP config sync failed; using current session config.", kind="warn")

    if not args:
        app.overlay.push(McpScreen(app))
        return

    if args != ["reconnect"]:
        app.chat_pane.append_notice("usage: /mcp [reconnect]", kind="warn")
        app.overlay.push(McpScreen(app))
        return

    if not getattr(app.agent_loop, "_mcp_servers", {}):
        app.chat_pane.append_notice("No MCP servers configured.", kind="warn")
        app.overlay.push(McpScreen(app))
        return

    if getattr(app.agent_loop, "_mcp_connecting", False):
        app.chat_pane.append_notice("MCP connection is already in progress.", kind="warn")
        app.overlay.push(McpScreen(app))
        return

    if getattr(app.agent_loop, "_mcp_connected", False):
        app.chat_pane.append_notice("MCP servers are already connected.", kind="info")
        app.overlay.push(McpScreen(app))
        return

    try:
        await app.agent_loop._connect_mcp()  # noqa: SLF001
    except Exception:
        app.chat_pane.append_notice("MCP reconnect failed; see logs for details.", kind="warn")
    else:
        stacks = getattr(app.agent_loop, "_mcp_stacks", {})
        if stacks:
            app.chat_pane.append_notice(
                f"MCP reconnected: {len(stacks)} server(s) connected.",
                kind="info",
            )
        else:
            app.chat_pane.append_notice("No MCP servers connected successfully.", kind="warn")
    app.overlay.push(McpScreen(app))


async def _cmd_stop(app: "TuiApp", args: list[str]) -> None:
    if app.state.in_flight_task and not app.state.in_flight_task.done():
        app.state.in_flight_task.cancel()
        app.chat_pane.append_notice(
            "Turn cancelled. The next message will close out the interrupted "
            "turn.",
            kind="warn",
        )


async def _cmd_restart(app: "TuiApp", args: list[str]) -> None:
    from uuid import uuid4
    await _cmd_stop(app, args)
    app.state.session_key = f"cli:tui:{uuid4().hex[:8]}"
    app.chat_pane.clear()
    app.chat_pane.append_notice("Agent context restarted.", kind="info")
    app.status_bar.refresh()


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("help", (), "Show command list", _cmd_help),
    SlashCommand("exit", ("quit",), "Leave the TUI", _cmd_exit),
    SlashCommand("clear", (), "Clear chat scroll", _cmd_clear),
    SlashCommand("new", (), "Start a new session", _cmd_new),
    SlashCommand(
        "sessions", ("session",), "Switch session", _cmd_sessions
    ),
    SlashCommand("model", ("models",), "Switch model", _cmd_model),
    SlashCommand(
        "provider", ("providers",), "Switch provider", _cmd_provider
    ),
    SlashCommand("theme", ("themes",), "Switch theme", _cmd_theme),
    SlashCommand("status", (), "Show status pane", _cmd_status),
    SlashCommand("mcp", (), "Show MCP status", _cmd_mcp),
    SlashCommand("stop", (), "Stop current turn", _cmd_stop),
    SlashCommand(
        "restart", (), "Restart agent context", _cmd_restart
    ),
)


def parse(line: str) -> tuple[SlashCommand | None, list[str]]:
    """Resolve a typed line to (command, argv). Non-slash lines and unknown
    or ambiguous-prefix slash lines return (None, []).
    """
    if not line.startswith("/"):
        return None, []
    raw = line[1:]
    if not raw.strip():
        return None, []
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None, []
    if not tokens:
        return None, []

    verb, *args = tokens

    # Exact name or alias.
    for cmd in SLASH_COMMANDS:
        if verb == cmd.name or verb in cmd.aliases:
            return cmd, args

    # Unique prefix match across canonical names.
    candidates = [c for c in SLASH_COMMANDS if c.name.startswith(verb)]
    if len(candidates) == 1:
        return candidates[0], args
    return None, []


async def dispatch(app: "TuiApp", line: str) -> bool:
    """Return True iff the line was a slash command (handled or rejected
    with a notice). False means the caller should send ``line`` to the agent.
    """
    if not line.startswith("/"):
        return False
    cmd, args = parse(line)
    if cmd is None:
        verb = line.split()[0] if line.split() else line
        app.chat_pane.append_notice(
            f"command not available in TUI; run `pythinker {verb.lstrip('/')} "
            "--help` from your shell",
            kind="warn",
        )
        return True
    await cmd.handler(app, args)
    return True
