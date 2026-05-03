"""Tests for multi-provider web search."""

import httpx
import pytest

from pythinker.agent.tools.web import WebSearchTool
from pythinker.config.schema import WebSearchConfig, WebSearchProviderConfig


def _tool(provider: str = "brave", api_key: str = "", base_url: str = "") -> WebSearchTool:
    providers = {}
    if api_key or base_url:
        slot = provider.strip().lower() or "brave"
        providers[slot] = WebSearchProviderConfig(api_key=api_key, base_url=base_url)
    return WebSearchTool(config=WebSearchConfig(provider=provider, providers=providers))


def _response(status: int = 200, json: dict | None = None) -> httpx.Response:
    """Build a mock httpx.Response with a dummy request attached."""
    r = httpx.Response(status, json=json)
    r._request = httpx.Request("GET", "https://mock")
    return r


def test_duckduckgo_search_is_exclusive():
    tool = _tool(provider="duckduckgo")
    assert tool.exclusive is True
    assert tool.concurrency_safe is False


def test_brave_with_api_key_remains_concurrency_safe():
    tool = _tool(provider="brave", api_key="brave-key")
    assert tool.exclusive is False
    assert tool.concurrency_safe is True


def test_brave_without_api_key_is_treated_as_duckduckgo_for_concurrency(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    tool = _tool(provider="brave", api_key="")
    assert tool.exclusive is True
    assert tool.concurrency_safe is False


@pytest.mark.asyncio
async def test_brave_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "brave" in url
        assert kw["headers"]["X-Subscription-Token"] == "brave-key"
        return _response(json={
            "web": {"results": [{"title": "PyThinker", "url": "https://example.com", "description": "AI assistant"}]}
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool(provider="brave", api_key="brave-key")
    result = await tool.execute(query="pythinker", count=1)
    assert "PyThinker" in result
    assert "https://example.com" in result


@pytest.mark.asyncio
async def test_tavily_search(monkeypatch):
    async def mock_post(self, url, **kw):
        assert "tavily" in url
        assert kw["headers"]["Authorization"] == "Bearer tavily-key"
        return _response(json={
            "results": [
                {"title": "Pythinker", "url": "https://pythinker.example", "content": "Framework"}
            ]
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    tool = _tool(provider="tavily", api_key="tavily-key")
    result = await tool.execute(query="pythinker")
    assert "Pythinker" in result
    assert "https://pythinker.example" in result


@pytest.mark.asyncio
async def test_searxng_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "searx.example" in url
        return _response(json={
            "results": [{"title": "Result", "url": "https://example.com", "content": "SearXNG result"}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool(provider="searxng", base_url="https://searx.example")
    result = await tool.execute(query="test")
    assert "Result" in result


@pytest.mark.asyncio
async def test_duckduckgo_search(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "DDG Result", "href": "https://ddg.example", "body": "From DuckDuckGo"}]

    monkeypatch.setattr("pythinker.agent.tools.web.DDGS", MockDDGS, raising=False)
    import pythinker.agent.tools.web as web_mod
    monkeypatch.setattr(web_mod, "DDGS", MockDDGS, raising=False)

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)

    tool = _tool(provider="duckduckgo")
    result = await tool.execute(query="hello")
    assert "DDG Result" in result


@pytest.mark.asyncio
async def test_brave_fallback_to_duckduckgo_when_no_key(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Fallback", "href": "https://ddg.example", "body": "DuckDuckGo fallback"}]

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    tool = _tool(provider="brave", api_key="")
    result = await tool.execute(query="test")
    assert "Fallback" in result


@pytest.mark.asyncio
async def test_jina_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "s.jina.ai" in str(url)
        assert kw["headers"]["Authorization"] == "Bearer jina-key"
        return _response(json={
            "data": [{"title": "Jina Result", "url": "https://jina.ai", "content": "AI search"}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool(provider="jina", api_key="jina-key")
    result = await tool.execute(query="test")
    assert "Jina Result" in result
    assert "https://jina.ai" in result


@pytest.mark.asyncio
async def test_kagi_search(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "kagi.com/api/v0/search" in url
        assert kw["headers"]["Authorization"] == "Bot kagi-key"
        assert kw["params"] == {"q": "test", "limit": 2}
        return _response(json={
            "data": [
                {"t": 0, "title": "Kagi Result", "url": "https://kagi.com", "snippet": "Premium search"},
                {"t": 1, "list": ["ignored related search"]},
            ]
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool(provider="kagi", api_key="kagi-key")
    result = await tool.execute(query="test", count=2)
    assert "Kagi Result" in result
    assert "https://kagi.com" in result
    assert "ignored related search" not in result


@pytest.mark.asyncio
async def test_unknown_provider():
    tool = _tool(provider="unknown")
    result = await tool.execute(query="test")
    assert "unknown" in result
    assert "Error" in result


@pytest.mark.asyncio
async def test_default_provider_is_brave(monkeypatch):
    async def mock_get(self, url, **kw):
        assert "brave" in url
        return _response(json={"web": {"results": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool(provider="", api_key="test-key")
    result = await tool.execute(query="test")
    assert "No results" in result


@pytest.mark.asyncio
async def test_searxng_no_base_url_falls_back(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Fallback", "href": "https://ddg.example", "body": "fallback"}]

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    tool = _tool(provider="searxng", base_url="")
    result = await tool.execute(query="test")
    assert "Fallback" in result


@pytest.mark.asyncio
async def test_searxng_invalid_url():
    tool = _tool(provider="searxng", base_url="not-a-url")
    result = await tool.execute(query="test")
    assert "Error" in result


@pytest.mark.asyncio
async def test_jina_422_falls_back_to_duckduckgo(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Fallback", "href": "https://ddg.example", "body": "DuckDuckGo fallback"}]

    async def mock_get(self, url, **kw):
        assert "s.jina.ai" in str(url)
        raise httpx.HTTPStatusError(
            "422 Unprocessable Entity",
            request=httpx.Request("GET", str(url)),
            response=httpx.Response(422, request=httpx.Request("GET", str(url))),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr("ddgs.DDGS", MockDDGS)

    tool = _tool(provider="jina", api_key="jina-key")
    result = await tool.execute(query="test")
    assert "DuckDuckGo fallback" in result


@pytest.mark.asyncio
async def test_kagi_fallback_to_duckduckgo_when_no_key(monkeypatch):
    class MockDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Fallback", "href": "https://ddg.example", "body": "DuckDuckGo fallback"}]

    monkeypatch.setattr("ddgs.DDGS", MockDDGS)
    monkeypatch.delenv("KAGI_API_KEY", raising=False)

    tool = _tool(provider="kagi", api_key="")
    result = await tool.execute(query="test")
    assert "Fallback" in result


@pytest.mark.asyncio
async def test_jina_search_uses_path_encoded_query(monkeypatch):
    calls = {}

    async def mock_get(self, url, **kw):
        calls["url"] = str(url)
        calls["params"] = kw.get("params")
        return _response(json={
            "data": [{"title": "Jina Result", "url": "https://jina.ai", "content": "AI search"}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool(provider="jina", api_key="jina-key")
    await tool.execute(query="hello world")
    assert calls["url"].rstrip("/") == "https://s.jina.ai/hello%20world"
    assert calls["params"] in (None, {})


@pytest.mark.asyncio
async def test_duckduckgo_timeout_returns_error(monkeypatch):
    """asyncio.wait_for guard should fire when DDG search hangs."""
    import threading
    gate = threading.Event()

    class HangingDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, max_results=5):
            gate.wait(timeout=10)
            return []

    monkeypatch.setattr("ddgs.DDGS", HangingDDGS)
    tool = _tool(provider="duckduckgo")
    tool.config.timeout = 0.2
    result = await tool.execute(query="test")
    gate.set()
    assert "Error" in result


def test_credentials_for_known_provider_returns_slot():
    cfg = WebSearchConfig(
        provider="brave",
        providers={"brave": WebSearchProviderConfig(api_key="BSA-1")},
    )
    creds = cfg.credentials_for("brave")
    assert creds.api_key == "BSA-1"


def test_credentials_for_missing_slot_returns_empty():
    cfg = WebSearchConfig(provider="brave", providers={})
    creds = cfg.credentials_for("brave")
    assert creds.api_key == ""
    assert creds.base_url == ""


def test_credentials_for_unknown_provider_returns_empty():
    cfg = WebSearchConfig(
        provider="brave",
        providers={"brave": WebSearchProviderConfig(api_key="BSA-1")},
    )
    creds = cfg.credentials_for("nonexistent")
    assert creds.api_key == ""
    assert creds.base_url == ""


def test_websearch_config_keeps_legacy_fields():
    """Legacy top-level api_key/base_url stay on the model for one minor version."""
    cfg = WebSearchConfig(provider="brave", api_key="legacy-key")
    assert cfg.api_key == "legacy-key"
    assert cfg.base_url == ""
    assert cfg.providers == {}


def test_credentials_for_blank_existing_slot_returns_same_instance():
    """If the user explicitly created an empty slot (e.g. to opt into env-var
    fallback), credentials_for() must return that same instance, not a
    fresh transient. This pins the contract `dict.get(...) or default`."""
    blank = WebSearchProviderConfig()
    cfg = WebSearchConfig(provider="brave", providers={"brave": blank})
    creds = cfg.credentials_for("brave")
    assert creds is blank


def _tool_new_shape(provider: str, **slot_kwargs) -> WebSearchTool:
    """Build a WebSearchTool using the new providers-dict shape."""
    slot = provider.strip().lower() or "brave"
    cfg = WebSearchConfig(
        provider=provider,
        providers={slot: WebSearchProviderConfig(**slot_kwargs)},
    )
    return WebSearchTool(config=cfg)


@pytest.mark.asyncio
async def test_brave_search_with_new_shape(monkeypatch):
    captured = {}

    async def mock_get(self, url, **kw):
        captured["url"] = url
        captured["headers"] = kw["headers"]
        return _response(json={
            "web": {"results": [{"title": "PT", "url": "https://e.com", "description": "x"}]}
        })

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = _tool_new_shape("brave", api_key="brave-new-key")
    out = await tool.execute("hello", count=1)
    assert "PT" in out
    assert captured["headers"]["X-Subscription-Token"] == "brave-new-key"


@pytest.mark.asyncio
async def test_tavily_search_with_new_shape(monkeypatch):
    captured = {}

    async def mock_post(self, url, **kw):
        captured["headers"] = kw["headers"]
        return _response(json={"results": [{"title": "T", "url": "https://e.com", "content": "x"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    tool = _tool_new_shape("tavily", api_key="tvly-new-key")
    out = await tool.execute("hello", count=1)
    assert "T" in out
    assert captured["headers"]["Authorization"] == "Bearer tvly-new-key"


def test_brave_concurrency_safe_with_new_shape():
    tool = _tool_new_shape("brave", api_key="brave-new-key")
    assert tool.exclusive is False
    assert tool.concurrency_safe is True


def test_brave_env_var_fallback_with_empty_provider_slot(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "env-key")
    cfg = WebSearchConfig(
        provider="brave",
        providers={"brave": WebSearchProviderConfig()},  # explicit empty slot
    )
    tool = WebSearchTool(config=cfg)
    assert tool.exclusive is False  # env var counts as configured
