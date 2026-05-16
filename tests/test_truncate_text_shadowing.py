import inspect


def test_sanitize_persisted_blocks_truncate_text_shadowing_regression() -> None:
    """Regression: avoid bool param shadowing imported truncate_text.

    Buggy behavior (historical):
    - sanitize_persisted_blocks imports `truncate_text` from helpers
    - if the function's bool truncate param were named `truncate_text`, calling
      with `truncate_text=True` would execute `truncate_text(text, ...)` and
      raise `TypeError: 'bool' object is not callable`.

    Asserts the safe parameter name on both the canonical TurnWriter method
    and the AgentLoop delegate that forwards to it, and exercises the
    truncation path end-to-end through TurnWriter (where the helper now lives).
    """

    from pythinker.agent.loop import AgentLoop
    from pythinker.agent.turn_writer import TurnWriter

    delegate_sig = inspect.signature(AgentLoop._sanitize_persisted_blocks)
    assert "should_truncate_text" in delegate_sig.parameters
    assert "truncate_text" not in delegate_sig.parameters

    impl_sig = inspect.signature(TurnWriter.sanitize_persisted_blocks)
    assert "should_truncate_text" in impl_sig.parameters
    assert "truncate_text" not in impl_sig.parameters

    writer = TurnWriter(max_tool_result_chars=5)
    content = [{"type": "text", "text": "0123456789"}]
    out = writer.sanitize_persisted_blocks(content, should_truncate_text=True)
    assert isinstance(out, list)
    assert out and out[0]["type"] == "text"
    assert isinstance(out[0]["text"], str)
    assert out[0]["text"] != content[0]["text"]

