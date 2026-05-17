from pythinker.providers.base import LLMProvider, LLMResponse
from pythinker.providers.model_metadata import get_model_metadata
from pythinker.providers.openai_codex_provider import OpenAICodexProvider


class DummyProvider(LLMProvider):
    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, reasoning_effort=None, tool_choice=None):
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "dummy"


def test_base_metadata_protocol_defaults_are_empty():
    provider = DummyProvider()
    assert provider.count_tokens_supported("anything") is False


async def test_base_list_model_metadata_defaults_empty():
    provider = DummyProvider()
    assert await provider.list_model_metadata() == []
    assert await provider.get_model_metadata("anything") is None


async def test_codex_provider_can_surface_static_metadata():
    provider = OpenAICodexProvider()
    meta = await provider.get_model_metadata("openai-codex/gpt-5.5")
    assert meta == get_model_metadata("openai-codex/gpt-5.5")
