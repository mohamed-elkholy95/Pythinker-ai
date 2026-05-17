"""Every built-in tool advertises whether old results may be microcompacted."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pythinker.agent.tools.cron import CronTool
from pythinker.agent.tools.filesystem import ReadFileTool
from pythinker.agent.tools.message import MessageTool
from pythinker.agent.tools.registry import ToolRegistry
from pythinker.agent.tools.spawn import SpawnTool
from pythinker.agent.tools.web import WebFetchTool, WebSearchTool


def _registry(tmp_path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ReadFileTool(workspace=tmp_path))
    reg.register(MessageTool(send_callback=AsyncMock()))
    reg.register(SpawnTool(manager=MagicMock()))
    reg.register(CronTool(MagicMock()))
    reg.register(WebSearchTool())
    reg.register(WebFetchTool())
    return reg


def test_read_file_is_compactable(tmp_path):
    tool = _registry(tmp_path).get("read_file")
    assert tool is not None
    assert tool.compactable is True


def test_message_is_not_compactable(tmp_path):
    tool = _registry(tmp_path).get("message")
    assert tool is not None
    assert tool.compactable is False


def test_default_compactable_is_true():
    from pythinker.agent.tools.base import Tool

    assert Tool.compactable is True


def test_non_compactable_allowlist_locked(tmp_path):
    expected = frozenset({"message", "spawn", "cron", "web_search", "web_fetch"})
    reg = _registry(tmp_path)
    actual = {name for name, tool in reg.items() if not tool.compactable}
    assert actual == expected
