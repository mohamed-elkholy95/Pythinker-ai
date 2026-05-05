"""Default-tool registration for the agent loop.

Lifted from ``pythinker/agent/loop.py`` so the loop file stays focused on
lifecycle and dispatch. The functions take an ``AgentLoop`` and mutate
``loop.tools`` / ``loop._browser_manager`` in place.

``register_default_tools`` and ``register_browser_tool`` go through
``loop._register_browser_tool`` (the method) so existing test patches that
substitute ``loop._register_browser_tool = MagicMock()`` (see
``tests/agent/test_loop_browser_wiring.py``) keep working.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from pythinker.agent.skills import BUILTIN_SKILLS_DIR
from pythinker.agent.tools.cron import CronTool
from pythinker.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from pythinker.agent.tools.message import MessageTool
from pythinker.agent.tools.notebook import NotebookEditTool
from pythinker.agent.tools.pdf import MakePdfTool
from pythinker.agent.tools.search import GlobTool, GrepTool
from pythinker.agent.tools.shell import ExecTool
from pythinker.agent.tools.spawn import SpawnTool
from pythinker.agent.tools.web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from pythinker.agent.loop import AgentLoop
    from pythinker.config.schema import BrowserConfig


def browser_storage_dir(config: "BrowserConfig") -> Path:
    if config.storage_state_dir:
        return Path(config.storage_state_dir).expanduser()
    from pythinker.config.paths import get_browser_storage_dir

    return get_browser_storage_dir()


def register_browser_tool(loop: "AgentLoop", config: "BrowserConfig") -> None:
    loop.tools.unregister("browser")
    loop._browser_manager = None
    if not loop.web_config.enable or not config.enable:
        return

    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        logger.warning(
            "browser tool requested but playwright is not installed; "
            "reinstall or upgrade pythinker-ai to restore the packaged browser dependency."
        )
        return

    from pythinker.agent.browser.manager import BrowserSessionManager
    from pythinker.agent.tools.browser import BrowserTool

    storage_dir = loop._browser_storage_dir(config)
    storage_dir.mkdir(parents=True, exist_ok=True)
    loop._browser_manager = BrowserSessionManager(config, storage_dir=storage_dir)
    loop.tools.register(BrowserTool(loop._browser_manager))


def register_default_tools(loop: "AgentLoop") -> None:
    """Register the default set of tools onto ``loop.tools``.

    Browser registration goes through ``loop._register_browser_tool`` (the
    method) so test mocks against that attribute reach the call site.
    """
    allowed_dir = (
        loop.workspace if (loop.restrict_to_workspace or loop.exec_config.sandbox) else None
    )
    extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
    loop.tools.register(
        ReadFileTool(
            workspace=loop.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
        )
    )
    for cls in (WriteFileTool, EditFileTool, ListDirTool):
        loop.tools.register(cls(workspace=loop.workspace, allowed_dir=allowed_dir))
    for cls in (GlobTool, GrepTool):
        loop.tools.register(cls(workspace=loop.workspace, allowed_dir=allowed_dir))
    loop.tools.register(NotebookEditTool(workspace=loop.workspace, allowed_dir=allowed_dir))
    loop.tools.register(MakePdfTool(workspace=loop.workspace, allowed_dir=allowed_dir))
    if loop.exec_config.enable:
        loop.tools.register(
            ExecTool(
                working_dir=str(loop.workspace),
                timeout=loop.exec_config.timeout,
                restrict_to_workspace=loop.restrict_to_workspace,
                sandbox=loop.exec_config.sandbox,
                path_append=loop.exec_config.path_append,
                allowed_env_keys=loop.exec_config.allowed_env_keys,
            )
        )
    if loop.web_config.enable:
        loop.tools.register(
            WebSearchTool(config=loop.web_config.search, proxy=loop.web_config.proxy)
        )
        loop.tools.register(WebFetchTool(proxy=loop.web_config.proxy))
        loop._register_browser_tool(loop.web_config.browser)
    loop.tools.register(MessageTool(send_callback=loop.bus.publish_outbound))
    loop.tools.register(SpawnTool(manager=loop.subagents))
    if loop.cron_service:
        loop.tools.register(
            CronTool(loop.cron_service, default_timezone=loop.context.timezone or "UTC")
        )
