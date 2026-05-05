"""Formatting helpers shared across built-in command handlers."""

from __future__ import annotations


def _escape_markdown_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _fenced_text(content: str) -> str:
    safe_content = content.replace("```", "`\\`\\`")
    return f"```text\n{safe_content}\n```"


def _format_task_row(record) -> str:
    return (
        f"- `{record.task_id}` {record.status} - {_escape_markdown_text(record.label)} "
        f"(updated {record.updated_at})"
    )
