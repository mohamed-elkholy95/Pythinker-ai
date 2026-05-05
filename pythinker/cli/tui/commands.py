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


async def _run_router_command(app: "TuiApp", handler, args: list[str]) -> None:
    """Run a router-style command handler and render its OutboundMessage.

    The router handlers (cmd_login / cmd_logout) take a CommandContext built
    around an InboundMessage. The TUI doesn't have a real inbound — synthesize
    a minimal one keyed to the current session, dispatch, then write the
    OutboundMessage content back into the chat pane as a notice.
    """
    from pythinker.bus.events import InboundMessage
    from pythinker.command.router import CommandContext

    session_key = getattr(app.state, "session_key", "cli:tui")
    msg = InboundMessage(
        channel="cli",
        sender_id="local",
        chat_id=session_key,
        content="",
        session_key_override=session_key,
    )
    ctx = CommandContext(
        msg=msg,
        session=None,
        key=session_key,
        raw="",
        args=" ".join(args),
        loop=getattr(app, "agent_loop", None),
    )
    out = await handler(ctx)
    text = (out.content if out is not None else "").strip()
    if text:
        kind = "warn" if "Could not" in text or "Unknown" in text else "info"
        app.chat_pane.append_notice(text, kind=kind)


async def _cmd_login(app: "TuiApp", args: list[str]) -> None:
    """``/login`` — open the provider picker; auth happens on-select.

    The picker now owns the auth UX: rows show provider name + auth-state
    badge, and selecting a row that's not yet ``ready`` runs the OAuth flow
    (Codex / Copilot) or prompts for an API key (everyone else) before
    switching the active provider. Direct ``/login <name>`` is intentionally
    not supported — pick from the menu so the same review-before-switch
    affordance applies.
    """
    del args  # picker is the single entry point — args ignored.
    from pythinker.cli.tui.pickers.provider import open_provider_picker

    await open_provider_picker(app)


async def _cmd_logout(app: "TuiApp", args: list[str]) -> None:
    """``/logout [provider]`` — clear stored credentials.

    Without a provider name, defers to the router handler (lists providers
    + prints usage). With a name: OAuth providers get their token file
    unlinked; api-key providers have ``providers.<name>.api_key`` cleared
    and the live provider is hot-reloaded so a stale key can't sneak into
    the next turn.
    """
    from pythinker.cli.tui.auth_actions import save_api_key_and_reload
    from pythinker.command.builtins.auth import cmd_logout as _logout
    from pythinker.providers.registry import find_by_name

    arg = args[0].strip() if args else ""
    if not arg:
        await _run_router_command(app, _logout, args)
        return

    spec = find_by_name(arg.replace("-", "_"))
    if spec is None or getattr(spec, "is_oauth", False):
        await _run_router_command(app, _logout, args)
        return

    if getattr(spec, "is_local", False) or getattr(spec, "is_direct", False):
        app.chat_pane.append_notice(
            f"{spec.label} has no api_key to clear.", kind="info"
        )
        return

    err = await save_api_key_and_reload(app, spec, "")
    if err:
        app.chat_pane.append_notice(
            f"Could not clear {spec.label} API key: {err}", kind="error"
        )
        return
    app.chat_pane.append_notice(
        f"✓ {spec.label} API key cleared.", kind="info"
    )
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
    SlashCommand(
        "login", (), "Show OAuth auth state and how to (re-)authenticate",
        _cmd_login,
    ),
    SlashCommand(
        "logout", (), "Delete the stored OAuth token for a provider",
        _cmd_logout,
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
            f"unknown command: {verb}. Type /help for the command list.",
            kind="warn",
        )
        return True
    await cmd.handler(app, args)
    return True
