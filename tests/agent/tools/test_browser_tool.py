"""Unit tests for BrowserTool's schema, validation, and dispatch."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pythinker.agent.tools.browser import BrowserTool


def _mk_state(key="k:1"):
    state = MagicMock()
    state.effective_key = key
    state.lock = asyncio.Lock()
    state.last_used_at = 0.0
    state.last_url = "about:blank"
    state.blocked_this_action = 0
    state.notify_restart_prefix = None
    state.enforce_page_limit = AsyncMock(return_value=0)
    state.default_timeout_ms = 15_000
    state.navigation_timeout_ms = 30_000
    state.eval_timeout_ms = 5_000
    state.snapshot_max_chars = 20_000
    page = MagicMock()
    page.url = "https://example.com/"
    page.title = AsyncMock(return_value="Example")
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.evaluate = AsyncMock(return_value="hello world")
    page.screenshot = AsyncMock(return_value=b"\x89PNG-fake")
    page.locator = MagicMock(return_value=MagicMock(press=AsyncMock(), hover=AsyncMock()))
    page.keyboard = MagicMock(press=AsyncMock())
    page.wait_for_selector = AsyncMock()
    state.page = page
    return state


def _mk_manager(state):
    mgr = MagicMock()
    mgr.acquire = AsyncMock(return_value=state)
    mgr.close_session = AsyncMock()
    return mgr


def _mk_tool(state):
    mgr = _mk_manager(state)
    tool = BrowserTool(mgr)
    tool.set_context("cli", "direct", effective_key="cli:direct")
    return tool, mgr


def test_action_enum_includes_all_v1_actions():
    tool, _ = _mk_tool(_mk_state())
    enum = tool.parameters["properties"]["action"]["enum"]
    assert set(enum) == {
        "navigate", "click", "type", "press", "hover",
        "snapshot", "screenshot", "evaluate", "close",
    }


def test_validate_params_navigate_requires_url():
    tool, _ = _mk_tool(_mk_state())
    errs = tool.validate_params({"action": "navigate"})
    assert any("url is required" in e for e in errs)


def test_validate_params_type_requires_selector_and_text():
    tool, _ = _mk_tool(_mk_state())
    errs = tool.validate_params({"action": "type"})
    assert any("selector is required" in e for e in errs)
    assert any("text is required" in e for e in errs)


def test_validate_params_press_requires_key():
    tool, _ = _mk_tool(_mk_state())
    errs = tool.validate_params({"action": "press"})
    assert any("key is required" in e for e in errs)


def test_validate_params_evaluate_requires_script():
    tool, _ = _mk_tool(_mk_state())
    errs = tool.validate_params({"action": "evaluate"})
    assert any("script is required" in e for e in errs)


def test_concurrency_safe_is_false():
    tool, _ = _mk_tool(_mk_state())
    assert tool.read_only is False
    assert tool.concurrency_safe is False


async def test_execute_navigate_blocks_ssrf(monkeypatch):
    state = _mk_state()
    tool, _ = _mk_tool(state)
    monkeypatch.setattr(
        "pythinker.agent.tools.browser.validate_url_target",
        lambda url: (False, "private IP not allowed"),
    )
    result = await tool.execute(action="navigate", url="http://10.0.0.1/")
    assert result.startswith("Error: blocked")
    state.page.goto.assert_not_awaited()


async def test_execute_navigate_returns_loaded_summary(monkeypatch):
    state = _mk_state()
    tool, _ = _mk_tool(state)
    monkeypatch.setattr(
        "pythinker.agent.tools.browser.validate_url_target",
        lambda url: (True, ""),
    )
    result = await tool.execute(action="navigate", url="https://example.com/")
    assert "loaded" in result
    assert "example.com" in result
    state.page.goto.assert_awaited_once()
    assert state.last_used_at > 0


async def test_execute_click_then_type():
    state = _mk_state()
    tool, _ = _mk_tool(state)
    r1 = await tool.execute(action="click", selector="#go")
    r2 = await tool.execute(action="type", selector="#email", text="x@y.z")
    assert "clicked" in r1
    assert "typed" in r2


async def test_execute_press_with_selector():
    state = _mk_state()
    tool, _ = _mk_tool(state)
    result = await tool.execute(action="press", key="Enter", selector="#search")
    assert "pressed" in result
    state.page.locator.assert_called_once_with("#search")


async def test_execute_press_without_selector_uses_keyboard():
    state = _mk_state()
    tool, _ = _mk_tool(state)
    result = await tool.execute(action="press", key="Tab")
    assert "pressed" in result
    state.page.keyboard.press.assert_awaited_once_with("Tab")


async def test_execute_screenshot_returns_image_url_block(monkeypatch):
    state = _mk_state()
    tool, _ = _mk_tool(state)
    monkeypatch.setattr(
        "pythinker.agent.tools.browser.validate_url_target",
        lambda url: (True, ""),
    )
    blocks = await tool.execute(action="screenshot")
    assert isinstance(blocks, list)
    # build_image_content_blocks returns "image_url", not "image".
    assert any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)
    # And a trailing text label.
    assert any(isinstance(b, dict) and b.get("type") == "text" for b in blocks)


async def test_execute_appends_page_limit_notice_to_text_result():
    state = _mk_state()
    state.enforce_page_limit = AsyncMock(return_value=2)
    tool, _ = _mk_tool(state)

    result = await tool.execute(action="snapshot")

    assert "closed 2 extra browser page" in result


async def test_execute_close_calls_manager():
    state = _mk_state()
    tool, mgr = _mk_tool(state)
    result = await tool.execute(action="close")
    assert "closed" in result
    mgr.close_session.assert_awaited_once_with("cli:direct")


async def test_set_context_distinct_keys_isolate_state():
    """Calling set_context with two keys must not collapse them in storage."""
    state_a = _mk_state(key="a:1")
    state_b = _mk_state(key="b:1")
    mgr = MagicMock()
    mgr.acquire = AsyncMock(side_effect=[state_a, state_b])
    mgr.close_session = AsyncMock()
    tool = BrowserTool(mgr)

    tool.set_context("a", "1", effective_key="a:1")
    await tool.execute(action="snapshot")
    tool.set_context("b", "1", effective_key="b:1")
    await tool.execute(action="snapshot")

    assert mgr.acquire.await_args_list[0].args[0] == "a:1"
    assert mgr.acquire.await_args_list[1].args[0] == "b:1"
