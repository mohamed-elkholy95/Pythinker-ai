import asyncio

from pythinker.utils.tokens import (
    async_estimate_prompt_tokens_chain,
    estimate_message_tokens,
    estimate_prompt_tokens,
)


def test_default_encoding_is_cl100k():
    msgs = [{"role": "user", "content": "hello world"}]
    n = estimate_prompt_tokens(msgs)
    assert n > 0


def test_o200k_encoding_changes_count_for_long_text():
    msgs = [{"role": "user", "content": "hello 世界 " * 200}]
    cl100k = estimate_prompt_tokens(msgs, encoding="cl100k_base")
    o200k = estimate_prompt_tokens(msgs, encoding="o200k_base")
    assert cl100k > 0
    assert o200k > 0
    assert cl100k != o200k


def test_unknown_encoding_falls_back_silently():
    msgs = [{"role": "user", "content": "hello"}]
    n = estimate_prompt_tokens(msgs, encoding="not_a_real_encoding")
    assert n > 0


def test_estimate_message_tokens_accepts_encoding():
    msg = {"role": "user", "content": "hello world"}
    n_default = estimate_message_tokens(msg)
    n_o200k = estimate_message_tokens(msg, encoding="o200k_base")
    assert n_default > 0
    assert n_o200k > 0


def test_async_chain_uses_async_provider_counter():
    class Provider:
        async def async_estimate_prompt_tokens(self, messages, tools, model):
            return 123, "async_counter"

    out = asyncio.run(async_estimate_prompt_tokens_chain(
        Provider(), "claude-opus-4-7", [{"role": "user", "content": "x"}], None,
    ))
    assert out == (123, "async_counter")
