"""Utility functions for pythinker.

This module re-exports all public helpers from their canonical submodules.
Import directly from submodules for new code; this shim preserves backward
compatibility for existing callers.

The tool-result persistence group (`maybe_persist_tool_result` and its
private helpers) is defined here rather than in `tool_results.py` because
test suites monkeypatch `pythinker.utils.helpers._cleanup_tool_result_buckets`
and `pythinker.utils.helpers.logger`; those patches must affect the function
that calls them, which requires both names to live in this module's namespace.
"""

# ruff: noqa: F401
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from pythinker.utils.messages import (
    build_assistant_message,
    build_status_content,
    split_message,
)
from pythinker.utils.text import (
    build_image_content_blocks,
    find_legal_message_start,
    image_placeholder_text,
    stringify_text_blocks,
    strip_think,
    truncate_text,
)
from pythinker.utils.time import (
    _UNSAFE_CHARS,
    current_time_str,
    safe_filename,
    timestamp,
)
from pythinker.utils.tokens import (
    async_estimate_prompt_tokens_chain,
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_prompt_tokens_chain,
)
from pythinker.utils.workspace import (
    detect_image_mime,
    ensure_dir,
    sync_workspace_templates,
)

# ---------------------------------------------------------------------------
# Tool-result persistence — defined here so monkeypatching this module's
# `_cleanup_tool_result_buckets` and `logger` attributes works in tests.
# ---------------------------------------------------------------------------

_TOOL_RESULT_PREVIEW_CHARS = 1200
_TOOL_RESULTS_DIR = ".pythinker/tool-results"
_TOOL_RESULT_RETENTION_SECS = 7 * 24 * 60 * 60
_TOOL_RESULT_MAX_BUCKETS = 32


def _render_tool_result_reference(
    filepath: Path,
    *,
    original_size: int,
    preview: str,
    truncated_preview: bool,
) -> str:
    result = (
        f"[tool output persisted]\n"
        f"Full output saved to: {filepath}\n"
        f"Original size: {original_size} chars\n"
        f"Preview:\n{preview}"
    )
    if truncated_preview:
        result += "\n...\n(Read the saved file if you need the full output.)"
    return result


def _bucket_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cleanup_tool_result_buckets(root: Path, current_bucket: Path) -> None:
    siblings = [path for path in root.iterdir() if path.is_dir() and path != current_bucket]
    cutoff = time.time() - _TOOL_RESULT_RETENTION_SECS
    for path in siblings:
        if _bucket_mtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
    keep = max(_TOOL_RESULT_MAX_BUCKETS - 1, 0)
    siblings = [path for path in siblings if path.exists()]
    if len(siblings) <= keep:
        return
    siblings.sort(key=_bucket_mtime, reverse=True)
    for path in siblings[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def _write_text_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
) -> Any:
    """Persist oversized tool output and replace it with a stable reference string."""
    if workspace is None or max_chars <= 0:
        return content

    text_payload: str | None = None
    suffix = "txt"
    if isinstance(content, str):
        text_payload = content
    elif isinstance(content, list):
        text_payload = stringify_text_blocks(content)
        if text_payload is None:
            return content
        suffix = "json"
    else:
        return content

    if len(text_payload) <= max_chars:
        return content

    root = ensure_dir(workspace / _TOOL_RESULTS_DIR)
    bucket = ensure_dir(root / safe_filename(session_key or "default"))
    try:
        _cleanup_tool_result_buckets(root, bucket)
    except Exception as exc:
        logger.warning("Failed to clean stale tool result buckets in {}: {}", root, exc)
    path = bucket / f"{safe_filename(tool_call_id)}.{suffix}"
    if not path.exists():
        if suffix == "json" and isinstance(content, list):
            _write_text_atomic(path, json.dumps(content, ensure_ascii=False, indent=2))
        else:
            _write_text_atomic(path, text_payload)

    preview = text_payload[:_TOOL_RESULT_PREVIEW_CHARS]
    return _render_tool_result_reference(
        path,
        original_size=len(text_payload),
        preview=preview,
        truncated_preview=len(text_payload) > _TOOL_RESULT_PREVIEW_CHARS,
    )
