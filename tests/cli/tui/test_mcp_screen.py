from __future__ import annotations

from types import SimpleNamespace


class _FakeTools:
    def __init__(self, names: list[str]) -> None:
        self.tool_names = names


def _fake_app(
    *,
    servers: dict[str, object],
    stacks: dict[str, object],
    tools: list[str],
    connected: bool = False,
    connecting: bool = False,
    web_search_provider: str = "duckduckgo",
) -> object:
    return SimpleNamespace(
        config=SimpleNamespace(
            tools=SimpleNamespace(
                mcp_servers=servers,
                web=SimpleNamespace(search=SimpleNamespace(provider=web_search_provider)),
            )
        ),
        agent_loop=SimpleNamespace(
            _mcp_servers=servers,
            _mcp_stacks=stacks,
            _mcp_connected=connected,
            _mcp_connecting=connecting,
            tools=_FakeTools(tools),
        ),
    )


def test_collect_mcp_status_handles_empty_config() -> None:
    from pythinker.cli.tui.screens.mcp import collect_mcp_status

    status = collect_mcp_status(_fake_app(servers={}, stacks={}, tools=[]))

    assert status.configured_servers == []
    assert status.connected_servers == []
    assert status.capabilities_by_server == {}


def test_collect_mcp_status_groups_registered_tools_by_server() -> None:
    from pythinker.cli.tui.screens.mcp import collect_mcp_status

    servers = {"filesystem": object(), "github": object()}
    stacks = {"filesystem": object()}
    tools = ["read_file", "mcp_filesystem_read_file", "mcp_github_search_issues"]

    status = collect_mcp_status(
        _fake_app(servers=servers, stacks=stacks, tools=tools, connected=True)
    )

    assert status.configured_servers == ["filesystem", "github"]
    assert status.connected_servers == ["filesystem"]
    assert status.capabilities_by_server == {
        "filesystem": ["mcp_filesystem_read_file"],
        "github": ["mcp_github_search_issues"],
    }


def test_collect_mcp_status_falls_back_to_loop_servers_when_config_snapshot_is_stale() -> None:
    from pythinker.cli.tui.screens.mcp import collect_mcp_status

    app = _fake_app(servers={}, stacks={}, tools=[])
    app.agent_loop._mcp_servers = {"tavily": object()}

    status = collect_mcp_status(app)

    assert status.configured_servers == ["tavily"]


def test_mcp_screen_explains_tavily_web_search_is_not_mcp() -> None:
    from pythinker.cli.tui.screens.mcp import McpScreen

    screen = McpScreen(
        _fake_app(servers={}, stacks={}, tools=[], web_search_provider="tavily")
    )

    text = "".join(fragment for _style, fragment in screen.render())
    assert "web search" in text
    assert "tavily" in text
    assert "not an MCP server" in text


def test_mcp_screen_render_mentions_reconnect_hint() -> None:
    from pythinker.cli.tui.screens.mcp import McpScreen

    screen = McpScreen(_fake_app(servers={"filesystem": object()}, stacks={}, tools=[]))

    text = "".join(fragment for _style, fragment in screen.render())
    assert "mcp" in text
    assert "configured" in text
    assert "/mcp reconnect" in text
