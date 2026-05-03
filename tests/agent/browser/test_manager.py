"""Unit tests for BrowserSessionManager using fakes (no real browser)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.agent.browser.manager import BrowserSessionManager
from pythinker.config.schema import BrowserConfig


def _make_fake_browser():
    fake_browser = MagicMock()
    fake_browser.is_connected = MagicMock(return_value=True)
    fake_browser.close = AsyncMock()

    def make_context(**kwargs):
        ctx = MagicMock()
        ctx.set_default_timeout = MagicMock()
        ctx.route = AsyncMock()
        ctx.close = AsyncMock()
        ctx.storage_state = AsyncMock(return_value={"cookies": []})
        page = MagicMock()
        page.is_closed = MagicMock(return_value=False)
        ctx.new_page = AsyncMock(return_value=page)
        return ctx

    fake_browser.new_context = AsyncMock(side_effect=make_context)
    return fake_browser


def _make_fake_pw_factory(fake_browser):
    """Returns an async function that BrowserSessionManager will use as the pw entrypoint."""

    fake_pw = MagicMock()
    fake_pw.chromium = MagicMock()
    fake_pw.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)
    fake_pw.chromium.launch = AsyncMock(return_value=fake_browser)
    fake_pw.stop = AsyncMock()

    async def factory():
        return fake_pw

    return factory, fake_pw


@pytest.fixture
def cfg():
    return BrowserConfig(enable=True, cdp_url="http://fake:9222")


async def test_lazy_connect(cfg, tmp_path, monkeypatch):
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    assert mgr._browser is None
    state = await mgr.acquire("k1")
    assert mgr._browser is fake_browser
    assert state.effective_key == "k1"


async def test_per_key_isolation(cfg, tmp_path, monkeypatch):
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    a = await mgr.acquire("alice:1")
    b = await mgr.acquire("bob:2")
    assert a is not b
    assert a.effective_key == "alice:1"
    assert b.effective_key == "bob:2"
    assert fake_browser.new_context.await_count == 2


async def test_acquire_idempotent_for_same_key(cfg, tmp_path, monkeypatch):
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    a1 = await mgr.acquire("k")
    a2 = await mgr.acquire("k")
    assert a1 is a2
    assert fake_browser.new_context.await_count == 1


async def test_healthcheck_failure_surfaces_error(cfg, tmp_path, monkeypatch):
    cfg = BrowserConfig(enable=True, mode="cdp", cdp_url=cfg.cdp_url)
    factory, _ = _make_fake_pw_factory(_make_fake_browser())
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(False, "ConnectError: refused")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    with pytest.raises(RuntimeError, match="browser CDP endpoint unreachable"):
        await mgr.acquire("k")


async def test_auto_mode_launches_when_default_cdp_url_is_not_configured(tmp_path, monkeypatch):
    cfg = BrowserConfig(enable=True)
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    healthcheck = AsyncMock(return_value=(False, "not running"))
    monkeypatch.setattr("pythinker.agent.browser.manager.cdp_healthcheck", healthcheck)

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    await mgr.acquire("k")

    healthcheck.assert_not_awaited()
    fake_pw.chromium.connect_over_cdp.assert_not_awaited()
    fake_pw.chromium.launch.assert_awaited_once()


async def test_launch_mode_uses_playwright_launch(tmp_path, monkeypatch):
    cfg = BrowserConfig(enable=True, mode="launch")
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    await mgr.acquire("k")

    fake_pw.chromium.connect_over_cdp.assert_not_awaited()
    fake_pw.chromium.launch.assert_awaited_once()


async def test_auto_mode_falls_back_to_launch_when_configured_cdp_unreachable(
    tmp_path,
    monkeypatch,
):
    """auto + non-default cdpUrl + CDP unreachable → launch mode takes over."""
    cfg = BrowserConfig(enable=True, mode="auto", cdp_url="http://browser:9222")
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    healthcheck = AsyncMock(return_value=(False, "connection refused"))
    monkeypatch.setattr("pythinker.agent.browser.manager.cdp_healthcheck", healthcheck)

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    await mgr.acquire("k")

    healthcheck.assert_awaited_once()
    fake_pw.chromium.connect_over_cdp.assert_not_awaited()
    fake_pw.chromium.launch.assert_awaited_once()


async def test_launch_mode_sandbox_failure_recommends_cdp_mode(tmp_path):
    """Sandbox failures inside hardened containers must surface actionable guidance.

    The plan's Container/sandbox section explicitly requires the error to
    recommend `cdp` mode and mention the `PYTHINKER_BROWSER_NO_SANDBOX=1`
    escape hatch as a deliberate local override, not a hardened default.
    """
    cfg = BrowserConfig(enable=True, mode="launch")
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    fake_pw.chromium.launch = AsyncMock(
        side_effect=RuntimeError(
            "Failed to launch chromium: no usable sandbox! "
            "Update your kernel or see https://chromium.googlesource.com/...zygote..."
        )
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    with pytest.raises(RuntimeError) as excinfo:
        await mgr.acquire("k")

    msg = str(excinfo.value)
    assert "Chromium's sandbox could not start" in msg
    assert "mode='cdp'" in msg
    assert "pythinker-browser" in msg
    assert "PYTHINKER_BROWSER_NO_SANDBOX=1" in msg


async def test_parallel_acquire_for_distinct_keys_does_not_serialize_beyond_startup(
    tmp_path,
):
    """Parallel acquire() across distinct effective keys must overlap.

    Connect-lock serializes only the one-time browser launch. Once the
    browser is up, two acquire() calls for distinct keys must each get
    their own BrowserContext concurrently — they may NOT block on each
    other's new_context call.
    """
    cfg = BrowserConfig(enable=True, mode="launch")
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)

    # Gate the first new_context call until the second one has started.
    # If acquire() serialized beyond _connect_lock, the first call would
    # deadlock waiting for the second call which never arrives.
    second_started = asyncio.Event()
    original_new_context = fake_browser.new_context
    call_count = 0

    async def gated_new_context(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.wait_for(second_started.wait(), timeout=2.0)
        else:
            second_started.set()
        return await original_new_context(**kwargs)

    fake_browser.new_context = gated_new_context

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    state_a, state_b = await asyncio.gather(mgr.acquire("a"), mgr.acquire("b"))

    # Browser launched exactly once; shared across contexts.
    fake_pw.chromium.launch.assert_awaited_once()
    # Distinct contexts and storage paths.
    assert state_a.context is not state_b.context
    assert state_a.storage_path != state_b.storage_path
    assert "a" in mgr._states and "b" in mgr._states


async def test_launch_mode_auto_provisions_then_retries(tmp_path, monkeypatch):
    cfg = BrowserConfig(enable=True, mode="launch", auto_provision=True)
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    fake_pw.chromium.launch = AsyncMock(
        side_effect=[RuntimeError("Executable doesn't exist; run playwright install"), fake_browser]
    )
    provision = AsyncMock()

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    monkeypatch.setattr(mgr, "_provision_chromium", provision)
    state = await mgr.acquire("k")

    provision.assert_awaited_once()
    assert fake_pw.chromium.launch.await_count == 2
    assert state.notify_restart_prefix
    assert "provisioned Playwright Chromium" in state.notify_restart_prefix


async def test_close_session_saves_and_drops(cfg, tmp_path, monkeypatch):
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    state = await mgr.acquire("k")
    state.context.storage_state = AsyncMock(return_value={"cookies": [{"name": "c"}]})

    await mgr.close_session("k")
    assert "k" not in mgr._states
    assert state.storage_path.exists()


async def test_shutdown_closes_everything(cfg, tmp_path, monkeypatch):
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    await mgr.acquire("a")
    await mgr.acquire("b")

    await mgr.shutdown()
    assert mgr._browser is None
    assert mgr._states == {}
    fake_browser.close.assert_awaited()
    fake_pw.stop.assert_awaited()


async def test_provisioning_runs_outside_connect_lock(tmp_path, monkeypatch):
    """Regression: a missing-Chromium provision must not hold _connect_lock.

    Without this, a 30-300 s `playwright install` blocks every other chat
    waiting on its own browser context.
    """
    cfg = BrowserConfig(enable=True, mode="launch", auto_provision=True)
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)
    fake_pw.chromium.launch = AsyncMock(
        side_effect=[RuntimeError("Executable doesn't exist"), fake_browser]
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    seen_locked = []

    async def fake_provision():
        seen_locked.append(mgr._connect_lock.locked())

    monkeypatch.setattr(mgr, "_provision_chromium", fake_provision)
    await mgr.acquire("k")

    assert seen_locked == [False], (
        "_connect_lock must be released while _provision_chromium runs"
    )


async def test_shutdown_force_skips_per_context_close(tmp_path, monkeypatch):
    """force=True drops contexts immediately and just closes browser+playwright."""
    cfg = BrowserConfig(enable=True, mode="launch")
    fake_browser = _make_fake_browser()
    factory, fake_pw = _make_fake_pw_factory(fake_browser)

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    state = await mgr.acquire("hung")

    state_close = AsyncMock()
    state.close = state_close

    await mgr.shutdown(force=True)

    state_close.assert_not_awaited()
    assert mgr._states == {}
    assert mgr._browser is None
    fake_pw.stop.assert_awaited()


async def test_evict_idle_closes_expired_contexts(tmp_path):
    cfg = BrowserConfig(enable=True, mode="launch", idle_ttl_seconds=1)
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    state = await mgr.acquire("idle")
    state.last_used_at = time.monotonic() - 5

    closed = await mgr.evict_idle()
    assert closed == 1
    assert "idle" not in mgr._states
    assert state.closed is True


async def test_acquire_after_disconnect_creates_fresh_context(cfg, tmp_path, monkeypatch):
    """After CDP disconnect, acquire() must NOT return the stale state."""
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    s1 = await mgr.acquire("k")
    # Simulate disconnect.
    mgr._on_disconnected()
    assert s1.dead is True
    assert mgr._browser is None

    # The next acquire must connect a new browser and produce a *fresh* state.
    s2 = await mgr.acquire("k")
    assert s2 is not s1
    assert not s2.dead
    assert s2.notify_restart_prefix is not None
    assert "browser session was restarted" in s2.notify_restart_prefix


async def test_acquire_binds_ssrf_route_handler(cfg, tmp_path, monkeypatch):
    """The created context must have route('**/*', ...) installed."""
    fake_browser = _make_fake_browser()
    factory, _ = _make_fake_pw_factory(fake_browser)
    monkeypatch.setattr(
        "pythinker.agent.browser.manager.cdp_healthcheck",
        AsyncMock(return_value=(True, "")),
    )

    mgr = BrowserSessionManager(cfg, tmp_path, _pw_factory=factory)
    state = await mgr.acquire("k")
    # `context.route` must have been awaited exactly once with the wildcard pattern.
    state.context.route.assert_awaited_once()
    pattern = state.context.route.await_args.args[0]
    assert pattern == "**/*"
