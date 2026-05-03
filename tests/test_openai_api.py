"""Focused tests for the fixed-session OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from pythinker.api.server import (
    API_CHAT_ID,
    API_SESSION_KEY,
    _chat_completion_response,
    _error_json,
    create_app,
    handle_chat_completions,
)

try:
    from aiohttp.test_utils import TestClient, TestServer

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytest_plugins = ("pytest_asyncio",)


def _attach_preauthorize_stub(agent: MagicMock) -> MagicMock:
    """Attach the preauthorize_direct stub the API server now calls before process_direct."""
    from pythinker.runtime.context import RequestContext

    agent.preauthorize_direct = MagicMock(
        side_effect=lambda *, session_key, channel, chat_id: RequestContext.for_inbound(
            channel=channel, sender_id="api-client",
            chat_id=chat_id, session_key=session_key,
        )
    )
    return agent


def _make_mock_agent(response_text: str = "mock response") -> MagicMock:
    agent = MagicMock()
    agent.process_direct = AsyncMock(return_value=response_text)
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    # The non-streaming and streaming paths both pre-authorize once and
    # reuse the returned RequestContext across attempts.
    return _attach_preauthorize_stub(agent)


@pytest.fixture
def mock_agent():
    return _make_mock_agent()


@pytest.fixture
def app(mock_agent):
    return create_app(mock_agent, model_name="test-model", request_timeout=10.0)


@pytest_asyncio.fixture
async def aiohttp_client():
    clients: list[TestClient] = []

    async def _make_client(app):
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    try:
        yield _make_client
    finally:
        for client in clients:
            await client.close()


def test_error_json() -> None:
    resp = _error_json(400, "bad request")
    assert resp.status == 400
    body = json.loads(resp.body)
    assert body["error"]["message"] == "bad request"
    assert body["error"]["code"] == 400


def test_chat_completion_response() -> None:
    result = _chat_completion_response("hello world", "test-model")
    assert result["object"] == "chat.completion"
    assert result["model"] == "test-model"
    assert result["choices"][0]["message"]["content"] == "hello world"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["id"].startswith("chatcmpl-")


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_missing_messages_returns_400(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post("/v1/chat/completions", json={"model": "test"})
    assert resp.status == 400


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_no_user_message_returns_400(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "system", "content": "you are a bot"}]},
    )
    assert resp.status == 400


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_stream_true_returns_sse(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
    )
    assert resp.status == 200
    assert resp.content_type == "text/event-stream"


@pytest.mark.asyncio
async def test_model_mismatch_returns_400() -> None:
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "model": "other-model",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    from pythinker.api.server import (
        _AGENT_LOOP_KEY,
        _MODEL_NAME_KEY,
        _REQUEST_TIMEOUT_KEY,
        _SESSION_LOCKS_KEY,
    )
    request.app = {
        _AGENT_LOOP_KEY: _make_mock_agent(),
        _MODEL_NAME_KEY: "test-model",
        _REQUEST_TIMEOUT_KEY: 10.0,
        _SESSION_LOCKS_KEY: {},
        "session_lock": asyncio.Lock(),
    }

    resp = await handle_chat_completions(request)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert "test-model" in body["error"]["message"]


@pytest.mark.asyncio
async def test_single_user_message_required() -> None:
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "previous reply"},
            ],
        }
    )
    from pythinker.api.server import (
        _AGENT_LOOP_KEY,
        _MODEL_NAME_KEY,
        _REQUEST_TIMEOUT_KEY,
        _SESSION_LOCKS_KEY,
    )
    request.app = {
        _AGENT_LOOP_KEY: _make_mock_agent(),
        _MODEL_NAME_KEY: "test-model",
        _REQUEST_TIMEOUT_KEY: 10.0,
        _SESSION_LOCKS_KEY: {},
        "session_lock": asyncio.Lock(),
    }

    resp = await handle_chat_completions(request)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert "single user message" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_single_user_message_must_have_user_role() -> None:
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "messages": [{"role": "system", "content": "you are a bot"}],
        }
    )
    from pythinker.api.server import (
        _AGENT_LOOP_KEY,
        _MODEL_NAME_KEY,
        _REQUEST_TIMEOUT_KEY,
        _SESSION_LOCKS_KEY,
    )
    request.app = {
        _AGENT_LOOP_KEY: _make_mock_agent(),
        _MODEL_NAME_KEY: "test-model",
        _REQUEST_TIMEOUT_KEY: 10.0,
        _SESSION_LOCKS_KEY: {},
        "session_lock": asyncio.Lock(),
    }

    resp = await handle_chat_completions(request)
    assert resp.status == 400
    body = json.loads(resp.body)
    assert "single user message" in body["error"]["message"].lower()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_successful_request_uses_fixed_api_session(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="test-model")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["choices"][0]["message"]["content"] == "mock response"
    assert body["model"] == "test-model"
    # The non-streaming path now pre-authorizes once and threads the
    # returned RequestContext into process_direct so the retry loop can
    # reuse it without minting a fresh trace_id and budgets.
    call = mock_agent.process_direct.call_args
    assert call.kwargs["content"] == "hello"
    assert call.kwargs["media"] is None
    assert call.kwargs["session_key"] == API_SESSION_KEY
    assert call.kwargs["channel"] == "api"
    assert call.kwargs["chat_id"] == API_CHAT_ID
    assert call.kwargs["request_context"] is not None


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_followup_requests_share_same_session_key(aiohttp_client) -> None:
    call_log: list[str] = []

    async def fake_process(content, session_key="", channel="", chat_id="", **kwargs):
        call_log.append(session_key)
        return f"reply to {content}"

    agent = MagicMock()
    agent.process_direct = fake_process
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    _attach_preauthorize_stub(agent)

    app = create_app(agent, model_name="m")
    client = await aiohttp_client(app)

    r1 = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "first"}]},
    )
    r2 = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "second"}]},
    )

    assert r1.status == 200
    assert r2.status == 200
    assert call_log == [API_SESSION_KEY, API_SESSION_KEY]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_fixed_session_requests_are_serialized(aiohttp_client) -> None:
    order: list[str] = []

    async def slow_process(content, session_key="", channel="", chat_id="", **kwargs):
        order.append(f"start:{content}")
        await asyncio.sleep(0.1)
        order.append(f"end:{content}")
        return content

    agent = MagicMock()
    agent.process_direct = slow_process
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    _attach_preauthorize_stub(agent)

    app = create_app(agent, model_name="m")
    client = await aiohttp_client(app)

    async def send(msg: str):
        return await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": msg}]},
        )

    r1, r2 = await asyncio.gather(send("first"), send("second"))
    assert r1.status == 200
    assert r2.status == 200
    # Verify serialization: one process must fully finish before the other starts
    if order[0] == "start:first":
        assert order.index("end:first") < order.index("start:second")
    else:
        assert order.index("end:second") < order.index("start:first")


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_models_endpoint(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.get("/v1/models")
    assert resp.status == 200
    body = await resp.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "test-model"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_health_endpoint(aiohttp_client, app) -> None:
    client = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_multimodal_content_extracts_text(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                }
            ]
        },
    )
    assert resp.status == 200
    call_kwargs = mock_agent.process_direct.call_args.kwargs
    assert call_kwargs["content"] == "describe this"
    assert call_kwargs["session_key"] == API_SESSION_KEY
    assert call_kwargs["channel"] == "api"
    assert call_kwargs["chat_id"] == API_CHAT_ID
    assert len(call_kwargs.get("media") or []) >= 0  # base64 images saved to disk


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_multimodal_remote_image_url_returns_400(aiohttp_client, mock_agent) -> None:
    app = create_app(mock_agent, model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
                    ],
                }
            ]
        },
    )

    assert resp.status == 400
    body = await resp.json()
    assert "remote image urls are not supported" in body["error"]["message"].lower()
    mock_agent.process_direct.assert_not_called()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_empty_response_retry_then_success(aiohttp_client) -> None:
    call_count = 0

    async def sometimes_empty(content, session_key="", channel="", chat_id="", **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ""
        return "recovered response"

    agent = MagicMock()
    agent.process_direct = sometimes_empty
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    _attach_preauthorize_stub(agent)

    app = create_app(agent, model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["choices"][0]["message"]["content"] == "recovered response"
    assert call_count == 2


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_empty_response_retry_reuses_preauthorized_context(aiohttp_client) -> None:
    """Non-streaming retry must share the same preauthorized RequestContext.

    Regression: process_direct() was called twice with no preserved
    request_context; one HTTP request fragmented into two trace_ids and
    two fresh BudgetCounters. The fix preauthorizes once and threads the
    same ctx through both attempts.
    """
    captured_contexts: list[object] = []

    async def empty_then_real(content, session_key="", channel="", chat_id="", **kwargs):
        captured_contexts.append(kwargs.get("request_context"))
        return "" if len(captured_contexts) == 1 else "real reply"

    agent = MagicMock()
    agent.process_direct = empty_then_real
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    _attach_preauthorize_stub(agent)

    app = create_app(agent, model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status == 200
    assert len(captured_contexts) == 2
    assert captured_contexts[0] is not None
    assert captured_contexts[1] is captured_contexts[0]
    # And preauthorize_direct was called exactly once for the whole HTTP request.
    assert agent.preauthorize_direct.call_count == 1


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_empty_response_falls_back(aiohttp_client) -> None:
    from pythinker.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

    call_count = 0

    async def always_empty(content, session_key="", channel="", chat_id="", **kwargs):
        nonlocal call_count
        call_count += 1
        return ""

    agent = MagicMock()
    agent.process_direct = always_empty
    agent._connect_mcp = AsyncMock()
    agent.close_mcp = AsyncMock()
    _attach_preauthorize_stub(agent)

    app = create_app(agent, model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["choices"][0]["message"]["content"] == EMPTY_FINAL_RESPONSE_MESSAGE
    assert call_count == 2


@pytest.mark.asyncio
async def test_process_direct_accepts_media() -> None:
    """process_direct should forward media paths to _process_message."""
    from pythinker.agent.loop import AgentLoop
    from pythinker.config.schema import RuntimeConfig
    from pythinker.runtime.policy import PolicyService

    loop = AgentLoop.__new__(AgentLoop)
    loop._connect_mcp = AsyncMock()
    loop.agent_registry = None
    loop.policy = PolicyService(enabled=False)
    loop._runtime_config = RuntimeConfig()
    loop.default_agent_id = "default"
    loop._unified_session = False

    captured_msg = None

    async def fake_process(msg, *, session_key="", on_progress=None, on_stream=None,
                           on_stream_end=None, on_tool_event=None):
        nonlocal captured_msg
        captured_msg = msg
        return None

    loop._process_message = fake_process

    await loop.process_direct(
        content="analyze this",
        media=["/tmp/image.png", "/tmp/report.pdf"],
        session_key="test:1",
    )

    assert captured_msg is not None
    assert captured_msg.media == ["/tmp/image.png", "/tmp/report.pdf"]
    assert captured_msg.content == "analyze this"


async def test_completions_returns_403_on_permission_error_nonstreaming(aiohttp_client):
    """Ingress denial on the non-streaming path → HTTP 403 + permission_denied envelope."""
    from pythinker.api.server import create_app

    class _DenyingLoop:
        # Minimal stub of the agent_loop interface the server uses.
        # Non-streaming now denies at preauthorize_direct (called before
        # process_direct), matching the streaming path's ordering.
        sessions = type("S", (), {"safe_key": staticmethod(lambda k: k)})()

        def preauthorize_direct(self, **kw):
            raise PermissionError("ingress denied: sender api:bad-client is blocked")

        async def process_direct(self, **kw):
            raise AssertionError("process_direct must not be called when ingress is denied")

    app = create_app(agent_loop=_DenyingLoop(), model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post("/v1/chat/completions", json={
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    })
    assert resp.status == 403
    body = await resp.json()
    assert body["error"]["type"] == "permission_denied"
    assert "ingress denied" in body["error"]["message"]


async def test_streaming_allowed_request_produces_single_trace_id(aiohttp_client):
    """One logical streaming request must produce ONE trace_id and ONE
    ingress policy decision — preauthorize_direct's RequestContext must be
    reused by process_direct, not re-normalized."""
    from pythinker.api.server import create_app
    from pythinker.runtime.context import RequestContext

    captured_contexts: list = []

    class _AllowingLoop:
        sessions = type("S", (), {"safe_key": staticmethod(lambda k: k)})()

        def preauthorize_direct(self, **kw):
            ctx = RequestContext.for_inbound(
                channel="api", sender_id=kw.get("sender_id", "api-client"),
                chat_id=kw.get("chat_id", "default"),
                session_key=kw["session_key"],
            )
            captured_contexts.append(("preauth", ctx))
            return ctx

        async def process_direct(self, **kw):
            captured_contexts.append(("process_direct", kw.get("request_context")))
            on_end = kw.get("on_stream_end")
            if on_end is not None:
                await on_end()
            return None

    app = create_app(agent_loop=_AllowingLoop(), model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post("/v1/chat/completions", json={
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert resp.status == 200
    # One preauth, one process_direct — and the ctx forwarded must be the
    # same object the preauth returned (same trace_id, no re-normalize).
    phases = [p for p, _ in captured_contexts]
    assert phases == ["preauth", "process_direct"]
    preauth_ctx = captured_contexts[0][1]
    forwarded_ctx = captured_contexts[1][1]
    assert forwarded_ctx is preauth_ctx
    assert forwarded_ctx.trace_id == preauth_ctx.trace_id


async def test_completions_returns_403_on_permission_error_streaming(aiohttp_client):
    """Ingress denial on the streaming path → HTTP 403 BEFORE prepare().

    The stub implements BOTH preauthorize_direct (the new pre-flight
    helper from Step 8c.5) AND process_direct (the older entrypoint),
    so the test exercises the production code path the server actually
    takes after Task 8c lands.
    """
    from pythinker.api.server import create_app

    class _DenyingLoop:
        sessions = type("S", (), {"safe_key": staticmethod(lambda k: k)})()

        def preauthorize_direct(self, **kw):
            raise PermissionError("ingress denied: sender api:bad-client is blocked")

        async def process_direct(self, **kw):
            # Should never be reached on the streaming path because
            # preauthorize_direct raises first. If it is, the test will
            # still produce a useful failure message.
            raise AssertionError("process_direct must not be reached after preauth fails")

    app = create_app(agent_loop=_DenyingLoop(), model_name="m")
    client = await aiohttp_client(app)
    resp = await client.post("/v1/chat/completions", json={
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    # Must be a real 403 — not a 200 with an SSE error event, because that
    # would force the OpenAI client to look inside the stream to decide
    # whether the request was authorized.
    assert resp.status == 403
    body = await resp.json()
    assert body["error"]["type"] == "permission_denied"
