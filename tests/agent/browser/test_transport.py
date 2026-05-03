"""Tests for the CDP healthcheck helper."""

from unittest.mock import MagicMock

import httpx

from pythinker.agent.browser.transport import cdp_healthcheck


async def test_healthcheck_passes_on_200(monkeypatch):
    fake_resp = MagicMock(status_code=200)
    fake_resp.raise_for_status = MagicMock()

    class FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return fake_resp

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    ok, err = await cdp_healthcheck("http://localhost:9222")
    assert ok is True
    assert err == ""


async def test_healthcheck_fails_on_connection_refused(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    ok, err = await cdp_healthcheck("http://localhost:9222")
    assert ok is False
    assert "refused" in err.lower() or "connect" in err.lower()


async def test_healthcheck_strips_trailing_slash():
    """The CDP /json/version endpoint must always be appended cleanly."""
    from pythinker.agent.browser.transport import _build_version_url
    assert _build_version_url("http://h:9222") == "http://h:9222/json/version"
    assert _build_version_url("http://h:9222/") == "http://h:9222/json/version"
