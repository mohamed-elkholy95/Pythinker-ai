"""End-to-end browser integration. Gated; requires the pythinker-browser container."""

import os

import pytest

pytestmark = [pytest.mark.browser]

if os.environ.get("PYTHINKER_BROWSER_INTEGRATION") != "1":
    pytest.skip(
        "Integration test gated by PYTHINKER_BROWSER_INTEGRATION=1",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def cdp_url():
    return os.environ.get("PYTHINKER_BROWSER_CDP", "http://127.0.0.1:9222")


@pytest.fixture(scope="module")
def browser_url(browser_http_fixture):
    """URL Chromium-in-container can reach via host.docker.internal."""
    port = browser_http_fixture
    return f"http://host.docker.internal:{port}/index.html"


async def test_full_action_set(cdp_url, browser_url, tmp_path, monkeypatch):
    from pythinker.agent.browser.manager import BrowserSessionManager
    from pythinker.agent.tools.browser import BrowserTool
    from pythinker.config.schema import BrowserConfig
    from pythinker.security.network import configure_ssrf_whitelist

    # Allow Docker bridge / Docker Desktop gateway addresses.
    configure_ssrf_whitelist(["172.16.0.0/12", "192.168.65.0/24"])

    cfg = BrowserConfig(enable=True, cdp_url=cdp_url)
    mgr = BrowserSessionManager(cfg, tmp_path)
    tool = BrowserTool(mgr)
    tool.set_context("test", "1", effective_key="test:1")

    try:
        nav = await tool.execute(action="navigate", url=browser_url)
        assert "loaded" in nav
        snap = await tool.execute(action="snapshot")
        assert "Hello from fixture" in snap
        await tool.execute(action="click", selector="#email")
        await tool.execute(action="type", selector="#email", text="x@y.z")
        await tool.execute(action="press", key="Enter", wait_for="#out")
        snap2 = await tool.execute(action="snapshot")
        assert "x@y.z" in snap2
        await tool.execute(action="hover", selector="#reveal")
        snap3 = await tool.execute(action="snapshot")
        assert "menu visible" in snap3
        shot = await tool.execute(action="screenshot")
        assert isinstance(shot, list) and any(b.get("type") == "image_url" for b in shot)
        result = await tool.execute(action="evaluate", script="1 + 1")
        assert "2" in result
        await tool.execute(action="close")
    finally:
        await mgr.shutdown()
