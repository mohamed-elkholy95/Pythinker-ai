"""SlashCommand parser + dispatch table behavior."""
from __future__ import annotations

from types import SimpleNamespace


def test_known_canonical_resolves() -> None:
    from pythinker.cli.tui.commands import parse
    cmd, args = parse("/help")
    assert cmd is not None
    assert cmd.name == "help"
    assert args == []


def test_alias_resolves_to_canonical() -> None:
    from pythinker.cli.tui.commands import parse
    cmd, _ = parse("/quit")
    assert cmd is not None and cmd.name == "exit"


def test_unique_prefix_resolves() -> None:
    from pythinker.cli.tui.commands import parse
    cmd, _ = parse("/mod")
    assert cmd is not None and cmd.name == "model"


def test_ambiguous_prefix_returns_none() -> None:
    from pythinker.cli.tui.commands import parse
    # /s -> /sessions, /status, /stop -> ambiguous
    cmd, _ = parse("/s")
    assert cmd is None


def test_unknown_command_returns_none() -> None:
    from pythinker.cli.tui.commands import parse
    cmd, _ = parse("/login")
    assert cmd is None


def test_mcp_command_resolves() -> None:
    from pythinker.cli.tui.commands import parse

    cmd, args = parse("/mcp")

    assert cmd is not None
    assert cmd.name == "mcp"
    assert args == []


def test_mcp_reconnect_args_are_preserved() -> None:
    from pythinker.cli.tui.commands import parse

    cmd, args = parse("/mcp reconnect")

    assert cmd is not None
    assert cmd.name == "mcp"
    assert args == ["reconnect"]


def test_args_split_via_shlex() -> None:
    from pythinker.cli.tui.commands import parse
    cmd, args = parse('/clear --hard "extra arg"')
    assert cmd is not None and cmd.name == "clear"
    assert args == ["--hard", "extra arg"]


def test_non_slash_lines_are_not_commands() -> None:
    from pythinker.cli.tui.commands import parse
    cmd, _ = parse("hello world")
    assert cmd is None


def test_slash_commands_table_has_required_set() -> None:
    from pythinker.cli.tui.commands import SLASH_COMMANDS
    names = {c.name for c in SLASH_COMMANDS}
    assert names >= {
        "help", "exit", "clear", "new", "sessions",
        "model", "provider", "theme", "status",
        "stop", "restart", "mcp",
    }


async def test_mcp_reconnect_syncs_mcp_servers_from_disk(monkeypatch) -> None:
    from pythinker.cli.tui.commands import dispatch

    class FakeAgentLoop:
        def __init__(self) -> None:
            self._mcp_servers = {}
            self._mcp_stacks = {}
            self._mcp_connected = False
            self._mcp_connecting = False
            self.tools = SimpleNamespace(tool_names=[])
            self.seen_servers = None

        async def close_mcp(self) -> None:
            self._mcp_stacks.clear()

        async def _connect_mcp(self) -> None:
            self.seen_servers = dict(self._mcp_servers)
            self._mcp_stacks = {"tavily": object()} if self._mcp_servers else {}

    notices = []
    overlays = []
    app = SimpleNamespace(
        options=SimpleNamespace(config_path="/tmp/config.json", workspace=None),
        config=SimpleNamespace(tools=SimpleNamespace(mcp_servers={})),
        agent_loop=FakeAgentLoop(),
        chat_pane=SimpleNamespace(
            append_notice=lambda text, kind="info": notices.append((kind, text))
        ),
        overlay=SimpleNamespace(push=lambda screen: overlays.append(screen)),
    )
    fresh = SimpleNamespace(
        tools=SimpleNamespace(
            mcp_servers={"tavily": object()},
            web=SimpleNamespace(search=SimpleNamespace(provider="tavily")),
        )
    )

    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path: fresh)
    monkeypatch.setattr("pythinker.config.loader.resolve_config_env_vars", lambda config: config)

    handled = await dispatch(app, "/mcp reconnect")

    assert handled is True
    assert app.agent_loop.seen_servers == {"tavily": fresh.tools.mcp_servers["tavily"]}
    assert app.config is fresh
    assert overlays
    assert notices[-1][0] == "info"


async def test_mcp_reconnect_replaces_same_name_config_and_unregisters_old_tools(
    monkeypatch,
) -> None:
    from pythinker.cli.tui.commands import dispatch

    class FakeTools:
        def __init__(self) -> None:
            self.tool_names = ["read_file", "mcp_tavily_search", "mcp_tavily_extract"]
            self.unregistered = []

        def unregister(self, name: str) -> None:
            self.unregistered.append(name)
            self.tool_names.remove(name)

    class FakeAgentLoop:
        def __init__(self) -> None:
            self._mcp_servers = {"tavily": "old-command"}
            self._mcp_stacks = {"tavily": object()}
            self._mcp_connected = True
            self._mcp_connecting = False
            self.tools = FakeTools()
            self.close_count = 0
            self.seen_servers = None

        async def close_mcp(self) -> None:
            self.close_count += 1
            self._mcp_stacks.clear()

        async def _connect_mcp(self) -> None:
            self.seen_servers = dict(self._mcp_servers)
            self._mcp_connected = True
            self._mcp_stacks = {"tavily": object()}

    fresh = SimpleNamespace(
        tools=SimpleNamespace(
            mcp_servers={"tavily": "new-command"},
            web=SimpleNamespace(search=SimpleNamespace(provider="tavily")),
        )
    )
    app = SimpleNamespace(
        options=SimpleNamespace(config_path="/tmp/config.json", workspace=None),
        config=SimpleNamespace(tools=SimpleNamespace(mcp_servers={"tavily": "old-command"})),
        agent_loop=FakeAgentLoop(),
        chat_pane=SimpleNamespace(append_notice=lambda _text, kind="info": None),
        overlay=SimpleNamespace(push=lambda _screen: None),
    )

    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path: fresh)
    monkeypatch.setattr("pythinker.config.loader.resolve_config_env_vars", lambda config: config)

    handled = await dispatch(app, "/mcp reconnect")

    assert handled is True
    assert app.agent_loop.close_count == 1
    assert app.agent_loop.tools.unregistered == ["mcp_tavily_search", "mcp_tavily_extract"]
    assert app.agent_loop.seen_servers == {"tavily": "new-command"}
