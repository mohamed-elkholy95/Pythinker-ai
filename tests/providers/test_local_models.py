"""Tests for ``pythinker.providers.local_models``.

We never hit a real local server — every test stubs the HTTP layer with
``httpx.MockTransport`` so the assertions stay deterministic and fast.

Coverage focuses on the provider-specific shape parsing (LM Studio
native, Ollama tags + ps merge, OpenAI-compat fallback) plus the
short-TTL cache.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pythinker.providers import local_models as mod


@pytest.fixture(autouse=True)
def _reset_cache():
    mod.clear_cache()
    yield
    mod.clear_cache()


def _route(routes: dict[str, dict[str, Any]]):
    """Build a MockTransport handler that matches by URL path."""

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, payload in routes.items():
            if path == prefix or path.startswith(prefix):
                return httpx.Response(payload.get("status", 200), json=payload.get("body"))
        return httpx.Response(404, json={"error": f"no route for {path}"})

    return _handler


def _patch_client(monkeypatch, handler):
    """Replace ``httpx.AsyncClient`` with one bound to ``handler``."""
    real_init = httpx.AsyncClient.__init__

    def _patched_init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


# ---------------------------------------------------------------------------
# LM Studio
# ---------------------------------------------------------------------------


async def test_lm_studio_native_endpoint_parses_loaded_instances(monkeypatch) -> None:
    """LM Studio's /api/v1/models distinguishes loaded from downloaded."""
    handler = _route({
        "/api/v1/models": {
            "body": {
                "models": [
                    {
                        "key": "lmstudio-community/qwen2.5-7b-instruct",
                        "display_name": "Qwen 2.5 7B Instruct",
                        "loaded_instances": [{"id": "abc"}],
                        "size_bytes": 4_500_000_000,
                        "params_string": "7B",
                        "quantization": {"name": "Q4_K_M", "bits_per_weight": 4.5},
                    },
                    {
                        "key": "lmstudio-community/llama-3.2-3b",
                        "display_name": "Llama 3.2 3B",
                        "loaded_instances": [],
                        "size_bytes": 1_900_000_000,
                        "params_string": "3B",
                        "quantization": {"name": "Q4_0"},
                    },
                ]
            },
        },
    })
    _patch_client(monkeypatch, handler)

    models = await mod.list_local_models(
        provider_id="lm_studio", api_base="http://localhost:1234/v1"
    )

    assert len(models) == 2
    qwen, llama = models
    assert qwen.model_id == "lmstudio-community/qwen2.5-7b-instruct"
    assert qwen.loaded is True
    assert qwen.parameter_size == "7B"
    assert qwen.quantization == "Q4_K_M"
    assert llama.loaded is False


async def test_lm_studio_falls_back_to_v1_models_when_native_404(monkeypatch) -> None:
    """If the native endpoint is unavailable, we degrade to OpenAI-compat."""
    handler = _route({
        "/api/v1/models": {"status": 404, "body": {"error": "not found"}},
        "/v1/models": {
            "body": {
                "data": [
                    {"id": "fallback-model", "object": "model", "owned_by": "user"},
                ],
            },
        },
    })
    _patch_client(monkeypatch, handler)

    models = await mod.list_local_models(
        provider_id="lm_studio", api_base="http://localhost:1234/v1"
    )
    assert [m.model_id for m in models] == ["fallback-model"]
    assert models[0].loaded is False


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


async def test_ollama_merges_tags_and_ps_marking_running_models(monkeypatch) -> None:
    """Models in /api/ps are marked running; models only in /api/tags are
    downloaded but cold."""
    handler = _route({
        "/api/tags": {
            "body": {
                "models": [
                    {
                        "model": "gemma3", "name": "gemma3",
                        "size": 3_300_000_000,
                        "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"},
                    },
                    {
                        "model": "llama3.2", "name": "llama3.2",
                        "size": 2_000_000_000,
                        "details": {"parameter_size": "3B", "quantization_level": "Q4_0"},
                    },
                ]
            },
        },
        "/api/ps": {
            "body": {
                "models": [
                    {
                        "model": "gemma3", "name": "gemma3",
                        "size": 6_500_000_000,
                        "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"},
                    }
                ]
            },
        },
    })
    _patch_client(monkeypatch, handler)

    models = await mod.list_local_models(
        provider_id="ollama", api_base="http://localhost:11434/v1"
    )
    by_id = {m.model_id: m for m in models}
    assert set(by_id) == {"gemma3", "llama3.2"}
    assert by_id["gemma3"].loaded is True
    assert by_id["llama3.2"].loaded is False


async def test_ollama_falls_back_to_v1_when_native_unreachable(monkeypatch) -> None:
    handler = _route({
        "/api/tags": {"status": 500, "body": {"error": "boom"}},
        "/api/ps": {"status": 500, "body": {"error": "boom"}},
        "/v1/models": {
            "body": {
                "data": [{"id": "compat-only", "object": "model", "owned_by": "ollama"}]
            },
        },
    })
    _patch_client(monkeypatch, handler)

    models = await mod.list_local_models(
        provider_id="ollama", api_base="http://localhost:11434/v1"
    )
    assert [m.model_id for m in models] == ["compat-only"]


# ---------------------------------------------------------------------------
# Generic OpenAI-compat fallback
# ---------------------------------------------------------------------------


async def test_generic_openai_compat_picks_data_array(monkeypatch) -> None:
    """vLLM / OVMS / custom backends only need /v1/models to work."""
    handler = _route({
        "/v1/models": {
            "body": {
                "data": [
                    {"id": "vllm-1", "object": "model"},
                    {"id": "vllm-2", "object": "model"},
                ]
            },
        },
    })
    _patch_client(monkeypatch, handler)

    models = await mod.list_local_models(
        provider_id="vllm", api_base="http://localhost:8000/v1"
    )
    assert [m.model_id for m in models] == ["vllm-1", "vllm-2"]


async def test_unreachable_server_returns_empty_list(monkeypatch) -> None:
    """A connection refused must return [] and never raise into the caller."""
    def _refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_client(monkeypatch, _refuse)

    models = await mod.list_local_models(
        provider_id="lm_studio", api_base="http://localhost:1234/v1", timeout_s=0.1
    )
    assert models == []


# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------


async def test_short_ttl_cache_avoids_repeat_calls(monkeypatch) -> None:
    """Two calls within TTL hit the network exactly once."""
    calls = {"n": 0}

    def _counting(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": [{"id": "cached", "object": "model"}]})

    _patch_client(monkeypatch, _counting)

    a = await mod.list_local_models(
        provider_id="custom", api_base="http://x/v1", cache_ttl_s=60.0
    )
    b = await mod.list_local_models(
        provider_id="custom", api_base="http://x/v1", cache_ttl_s=60.0
    )
    assert a == b
    assert calls["n"] == 1


async def test_clear_cache_forces_refetch(monkeypatch) -> None:
    calls = {"n": 0}

    def _counting(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": [{"id": "x", "object": "model"}]})

    _patch_client(monkeypatch, _counting)

    await mod.list_local_models(
        provider_id="custom", api_base="http://x/v1", cache_ttl_s=60.0
    )
    mod.clear_cache()
    await mod.list_local_models(
        provider_id="custom", api_base="http://x/v1", cache_ttl_s=60.0
    )
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def test_coerce_iter_handles_none_and_iterables() -> None:
    assert mod.coerce_iter(None) == []
    items = [mod.LocalModel(model_id="a"), mod.LocalModel(model_id="b")]
    assert mod.coerce_iter(iter(items)) == items
