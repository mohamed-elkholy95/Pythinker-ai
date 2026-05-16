"""Tests for the public ContextBuilder.build_user_content() API.

This method is the public seam consumed by the agent loop's pending-message
drain and by build_messages(). Coverage focuses on the runtime_context merge
behavior — the underlying image-handling path is exercised in
test_context_documents.py via the private _build_user_content.
"""

from __future__ import annotations

from pathlib import Path

from pythinker.agent.context import ContextBuilder


def _make_builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, timezone="UTC")


def test_build_user_content_text_only_no_runtime_context(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    result = builder.build_user_content("hello", None)
    assert result == "hello"


def test_build_user_content_text_with_runtime_context_prepends_string(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    result = builder.build_user_content(
        "hello",
        None,
        runtime_context="[ctx]\nCurrent Time: 2026\n[/ctx]",
    )
    assert isinstance(result, str)
    assert result == "[ctx]\nCurrent Time: 2026\n[/ctx]\n\nhello"


def test_build_user_content_image_with_runtime_context_prepends_text_block(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    png = tmp_path / "test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    result = builder.build_user_content(
        "describe",
        [str(png)],
        runtime_context="RUNTIME_CTX_MARKER",
    )

    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": "RUNTIME_CTX_MARKER"}
    types = [block["type"] for block in result]
    assert "image_url" in types


def test_build_user_content_image_no_runtime_context_returns_image_blocks_only(
    tmp_path: Path,
) -> None:
    builder = _make_builder(tmp_path)
    png = tmp_path / "test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    result = builder.build_user_content("describe", [str(png)])

    assert isinstance(result, list)
    # No runtime_context: the first block must not be a synthetic runtime-context text block.
    assert result[0]["type"] in {"image_url", "text"}
    if result[0]["type"] == "text":
        assert "Current Time" not in result[0]["text"]
        assert "Runtime Context" not in result[0]["text"]


def test_build_messages_still_includes_runtime_context_via_public_api(tmp_path: Path) -> None:
    """build_messages should keep prepending runtime context after the refactor.

    Verifies the refactor of build_messages → build_user_content(..., runtime_context=...)
    preserves the existing behavior: the final user message carries the runtime context
    block via the shared public method.
    """
    builder = _make_builder(tmp_path)
    messages = builder.build_messages(
        history=[],
        current_message="hi",
        channel="cli",
        chat_id="c1",
    )
    user_msg = messages[-1]
    assert user_msg["role"] == "user"
    content = user_msg["content"]
    serialized = content if isinstance(content, str) else str(content)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in serialized
    assert "Current Time" in serialized
    assert "Channel: cli" in serialized
    assert "Chat ID: c1" in serialized
