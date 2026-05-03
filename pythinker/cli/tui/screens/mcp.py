"""MCP status overlay for the TUI."""

from __future__ import annotations

from dataclasses import dataclass

from pythinker.cli.tui.panes.overlay import OverlayScreen


@dataclass(frozen=True)
class McpStatus:
    configured_servers: list[str]
    connected_servers: list[str]
    connecting: bool
    connected: bool
    capabilities_by_server: dict[str, list[str]]
    web_search_provider: str | None = None


def _server_for_capability(name: str, server_names: list[str]) -> str:
    for server in sorted(server_names, key=len, reverse=True):
        if name.startswith(f"mcp_{server}_"):
            return server
    return "other"


def collect_mcp_status(app: object) -> McpStatus:
    config = getattr(app, "config", None)
    config_tools = getattr(config, "tools", None)
    agent_loop = getattr(app, "agent_loop", None)
    loop_tools = getattr(agent_loop, "tools", None)

    config_servers = getattr(config_tools, "mcp_servers", {}) or {}
    loop_servers = getattr(agent_loop, "_mcp_servers", {}) or {}
    configured = sorted((set(config_servers) | set(loop_servers)))
    stacks = getattr(agent_loop, "_mcp_stacks", {})
    connected = sorted(stacks.keys())
    tool_names = sorted(
        name for name in getattr(loop_tools, "tool_names", []) if name.startswith("mcp_")
    )
    known_servers = sorted(set(configured) | set(connected))
    grouped: dict[str, list[str]] = {}
    for tool_name in tool_names:
        server = _server_for_capability(tool_name, known_servers)
        grouped.setdefault(server, []).append(tool_name)

    return McpStatus(
        configured_servers=configured,
        connected_servers=connected,
        connecting=bool(getattr(agent_loop, "_mcp_connecting", False)),
        connected=bool(getattr(agent_loop, "_mcp_connected", False)),
        capabilities_by_server=grouped,
        web_search_provider=getattr(
            getattr(getattr(config_tools, "web", None), "search", None),
            "provider",
            None,
        ),
    )


class McpScreen(OverlayScreen):
    def __init__(self, app: object) -> None:
        # Collect once at open time — this is a static snapshot overlay.
        self._status = collect_mcp_status(app)

    def render(self) -> list[tuple[str, str]]:
        status = self._status
        out: list[tuple[str, str]] = [("class:status.brand", " mcp \n\n")]
        out.append(("", f"  configured servers : {len(status.configured_servers)}\n"))
        out.append(("", f"  connected servers  : {len(status.connected_servers)}\n"))
        state = "connecting" if status.connecting else "connected" if status.connected else "disconnected"
        out.append(("", f"  state              : {state}\n"))

        if status.configured_servers:
            out.append(("class:hint", "\n  configured\n"))
            for server in status.configured_servers:
                marker = "✓" if server in status.connected_servers else "·"
                out.append(("", f"    {marker} {server}\n"))
        else:
            out.append(("class:hint", "\n  no MCP servers configured\n"))

        if status.capabilities_by_server:
            out.append(("class:hint", "\n  capabilities\n"))
            for server, names in sorted(status.capabilities_by_server.items()):
                out.append(("", f"    {server}\n"))
                for name in names:
                    out.append(("class:hint", f"      {name}\n"))
        else:
            out.append(("class:hint", "\n  no MCP capabilities registered\n"))

        if status.web_search_provider:
            out.append(("class:hint", "\n  web search\n"))
            out.append(("", f"    provider: {status.web_search_provider}"))
            if status.web_search_provider == "tavily" and "tavily" not in status.configured_servers:
                out.append(
                    ("class:hint", " (configured as built-in web_search, not an MCP server)")
                )
            out.append(("", "\n"))

        out.append(("class:hint", "\n  /mcp reconnect to retry connections\n"))
        out.append(("class:hint", "  press Esc to close\n"))
        return out
