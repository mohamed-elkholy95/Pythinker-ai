"""Tests for the AgentLoop wiring of BrowserTool: registration, context, close."""

from unittest.mock import AsyncMock, MagicMock

from pythinker.agent.tools.base import Tool


async def test_close_browser_session_no_op_when_no_manager():
    """The public method must quietly no-op when [browser] is not installed."""
    from pythinker.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop._browser_manager = None
    await loop.close_browser_session("k")


async def test_close_browser_calls_manager_shutdown():
    from pythinker.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    fake_mgr = MagicMock()
    fake_mgr.shutdown = AsyncMock()
    loop._browser_manager = fake_mgr
    await loop.close_browser()
    fake_mgr.shutdown.assert_awaited_once()


async def test_set_tool_context_forwards_session_key():
    """When session_key is supplied, it overrides the channel:chat_id derivation."""
    from pythinker.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop._unified_session = False

    captured = {}

    class FakeBrowserTool(Tool):
        name = "browser"
        description = "x"
        @property
        def parameters(self): return {"type": "object"}
        async def execute(self, **kw): pass
        def set_context(self, channel, chat_id, *, effective_key=""):
            captured.update(channel=channel, chat_id=chat_id, effective_key=effective_key)

    class FakeRegistry:
        def __init__(self): self._tools = {"browser": FakeBrowserTool()}
        def get(self, n): return self._tools.get(n)

    loop.tools = FakeRegistry()
    loop._set_tool_context("telegram", "42", session_key="cron:abc-123")
    assert captured["effective_key"] == "cron:abc-123"


async def test_set_tool_context_falls_back_to_derivation():
    from pythinker.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop._unified_session = False

    captured = {}

    class FakeBrowserTool(Tool):
        name = "browser"
        description = "x"
        @property
        def parameters(self): return {"type": "object"}
        async def execute(self, **kw): pass
        def set_context(self, channel, chat_id, *, effective_key=""):
            captured["effective_key"] = effective_key

    class FakeRegistry:
        def __init__(self): self._tools = {"browser": FakeBrowserTool()}
        def get(self, n): return self._tools.get(n)

    loop.tools = FakeRegistry()
    loop._set_tool_context("slack", "C42")
    assert captured["effective_key"] == "slack:C42"


async def test_loop_hook_propagates_session_key_to_set_tool_context():
    """The mid-iteration re-call from _LoopHook must use the canonical session_key."""
    from pythinker.agent.loop import AgentLoop, _LoopHook

    loop = AgentLoop.__new__(AgentLoop)
    loop._unified_session = False

    captured = {"calls": []}

    class FakeBrowserTool(Tool):
        name = "browser"
        description = "x"
        @property
        def parameters(self): return {"type": "object"}
        async def execute(self, **kw): pass
        def set_context(self, channel, chat_id, *, effective_key=""):
            captured["calls"].append(effective_key)

    class FakeRegistry:
        def __init__(self): self._tools = {"browser": FakeBrowserTool()}
        def get(self, n): return self._tools.get(n)

    loop.tools = FakeRegistry()

    hook = _LoopHook(
        loop,
        channel="cli",
        chat_id="direct",
        message_id="m1",
        session_key="cron:job-xyz",
    )
    # Simulate the mid-iteration re-call directly via the same invocation the
    # hook makes in before_execute_tools.
    loop._set_tool_context(
        hook._channel, hook._chat_id, hook._message_id,
        session_key=hook._session_key,
    )
    assert captured["calls"] == ["cron:job-xyz"]


async def test_refresh_browser_config_rebuilds_manager_on_signature_change():
    from pythinker.agent.loop import AgentLoop
    from pythinker.config.schema import BrowserConfig, WebToolsConfig

    loop = AgentLoop.__new__(AgentLoop)
    old = BrowserConfig(enable=True, mode="launch")
    new = BrowserConfig(enable=True, mode="cdp", cdp_url="http://browser:9222")
    old_manager = MagicMock()
    old_manager.shutdown = AsyncMock()
    loop.web_config = WebToolsConfig(browser=old)
    loop._browser_signature = old.signature()
    loop._browser_config_loader = lambda: new
    loop._browser_manager = old_manager
    loop.tools = MagicMock()
    loop._register_browser_tool = MagicMock()

    await loop._refresh_browser_config()

    loop.tools.unregister.assert_called_once_with("browser")
    old_manager.shutdown.assert_awaited_once()
    assert loop.web_config.browser is new
    assert loop._browser_signature == new.signature()
    loop._register_browser_tool.assert_called_once_with(new)


async def test_refresh_browser_config_noops_when_signature_matches():
    from pythinker.agent.loop import AgentLoop
    from pythinker.config.schema import BrowserConfig, WebToolsConfig

    loop = AgentLoop.__new__(AgentLoop)
    cfg = BrowserConfig(enable=True)
    manager = MagicMock()
    manager.shutdown = AsyncMock()
    loop.web_config = WebToolsConfig(browser=cfg)
    loop._browser_signature = cfg.signature()
    loop._browser_config_loader = lambda: cfg
    loop._browser_manager = manager
    loop.tools = MagicMock()
    loop._register_browser_tool = MagicMock()

    await loop._refresh_browser_config()

    loop.tools.unregister.assert_not_called()
    manager.shutdown.assert_not_awaited()
    loop._register_browser_tool.assert_not_called()


def test_browser_storage_dir_prefers_configured_override(tmp_path):
    from pythinker.agent.loop import AgentLoop
    from pythinker.config.schema import BrowserConfig

    loop = AgentLoop.__new__(AgentLoop)
    custom = tmp_path / "browser-state"
    assert loop._browser_storage_dir(BrowserConfig(storage_state_dir=str(custom))) == custom
