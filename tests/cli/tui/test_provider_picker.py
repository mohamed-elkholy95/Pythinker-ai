from __future__ import annotations

import pytest

from pythinker.config.schema import Config


@pytest.mark.asyncio
async def test_local_provider_default_model_uses_openai_models_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pythinker.cli.tui.pickers import provider as provider_picker

    cfg = Config()
    cfg.providers.lm_studio.api_base = "http://localhost:1234/v1"

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "qwen/qwen3.6-27b"}]}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        async def get(self, url: str) -> _FakeResponse:
            assert url == "http://localhost:1234/v1/models"
            return _FakeResponse()

    monkeypatch.setattr(provider_picker.httpx, "AsyncClient", _FakeAsyncClient)

    model = await provider_picker._default_model_for("lm_studio", cfg)

    assert model == "qwen/qwen3.6-27b"


@pytest.mark.asyncio
async def test_local_provider_default_model_falls_back_to_none_when_models_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pythinker.cli.tui.pickers import provider as provider_picker

    cfg = Config()
    cfg.providers.lm_studio.api_base = "http://localhost:1234/v1"

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        async def get(self, url: str) -> object:
            raise OSError("server down")

    monkeypatch.setattr(provider_picker.httpx, "AsyncClient", _FakeAsyncClient)

    model = await provider_picker._default_model_for("lm_studio", cfg)

    assert model is None


def test_recommended_provider_default_model_still_uses_static_catalog() -> None:
    from pythinker.cli.tui.pickers.provider import _static_default_model_for

    assert _static_default_model_for("openai_codex") == "openai-codex/gpt-5.5"
