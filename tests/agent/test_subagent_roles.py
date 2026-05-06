"""Subagent role split — Phase 2 of the coding-prompt uplift.

`.agents/plans/2026-05-05-coding-prompt-uplift.md` Phase 2:

  * coder (default) — current full tool set: read/write/edit/shell/web.
  * explore         — read-only: read_file/list_dir/glob/grep + web; no
                      write_file, edit_file, shell tool.
  * plan            — same tools as explore; output structure differs
                      (known/unknown/plan).

Default behavior (no role passed) must be byte-identical to today.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pythinker.agent.subagent import SubagentManager
from pythinker.bus.queue import MessageBus
from pythinker.runtime.context import RequestContext


async def _spawn_and_capture_tools(
    tmp_path,
    *,
    role: str | None = None,
    enable_shell: bool = True,
):
    """Run spawn(role=role), drain the bg task, return registered tool names."""
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    parent_ctx = RequestContext.for_inbound(
        channel="cli", sender_id="u", chat_id="c", session_key="cli:c",
    )

    with patch("pythinker.agent.subagent.AgentRunner") as mock_runner:
        mock_runner.return_value.run = AsyncMock()
        sm = SubagentManager(
            provider=provider, workspace=tmp_path, bus=bus,
            max_tool_result_chars=4096, model="m",
        )
        sm.exec_config.enable = enable_shell

        spawn_kwargs = dict(
            task="do thing",
            label="t",
            origin_channel="cli",
            origin_chat_id="c",
            session_key="cli:c",
            parent_context=parent_ctx,
            parent_egress=object(),
        )
        if role is not None:
            spawn_kwargs["role"] = role

        await sm.spawn(**spawn_kwargs)
        await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)

        spec = mock_runner.return_value.run.call_args.args[0]
        return set(spec.tools.tool_names)


async def test_coder_role_default_keeps_full_tool_set(tmp_path):
    """No role arg = coder behavior, including write/edit/shell."""
    tools = await _spawn_and_capture_tools(tmp_path)
    for expected in ("read_file", "write_file", "edit_file", "list_dir", "glob", "grep", "exec"):
        assert expected in tools


async def test_coder_role_explicit_keeps_full_tool_set(tmp_path):
    tools = await _spawn_and_capture_tools(tmp_path, role="coder")
    for expected in ("write_file", "edit_file", "exec"):
        assert expected in tools


async def test_explore_role_drops_write_edit_shell(tmp_path):
    """Explore subagent must NOT have write_file / edit_file / shell tool."""
    tools = await _spawn_and_capture_tools(tmp_path, role="explore")
    for expected in ("read_file", "list_dir", "glob", "grep"):
        assert expected in tools
    for forbidden in ("write_file", "edit_file", "exec"):
        assert forbidden not in tools


async def test_plan_role_drops_write_edit_shell(tmp_path):
    """Plan subagent must NOT have write_file / edit_file / shell tool."""
    tools = await _spawn_and_capture_tools(tmp_path, role="plan")
    for expected in ("read_file", "list_dir", "glob", "grep"):
        assert expected in tools
    for forbidden in ("write_file", "edit_file", "exec"):
        assert forbidden not in tools


async def test_unknown_role_falls_back_to_coder(tmp_path):
    """Defensive: unknown role string still gives a working coder subagent."""
    tools = await _spawn_and_capture_tools(tmp_path, role="bogus")
    assert "write_file" in tools
    assert "exec" in tools


@pytest.mark.parametrize("role", ["coder", "explore", "plan"])
def test_build_subagent_prompt_includes_role_section(tmp_path, role):
    """Each role injects a recognizable header into the rendered prompt."""
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    sm = SubagentManager(
        provider=provider, workspace=tmp_path, bus=bus,
        max_tool_result_chars=4096, model="m",
    )
    rendered = sm._build_subagent_prompt(role=role)
    assert "# Subagent" in rendered
    if role == "coder":
        assert "## Coding subagent" in rendered
    elif role == "explore":
        assert "## Explore subagent" in rendered
        assert "read-only" in rendered.lower()
    elif role == "plan":
        assert "## Plan subagent" in rendered
        assert "Known" in rendered


def test_build_subagent_prompt_unknown_role_falls_back_to_coder(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    sm = SubagentManager(
        provider=provider, workspace=tmp_path, bus=bus,
        max_tool_result_chars=4096, model="m",
    )
    rendered = sm._build_subagent_prompt(role="ghost")
    assert "## Coding subagent" in rendered


def test_build_subagent_prompt_skills_only_for_coder(tmp_path, monkeypatch):
    """Explore / plan suppress skills_summary so write/exec patterns don't leak in."""
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    sm = SubagentManager(
        provider=provider, workspace=tmp_path, bus=bus,
        max_tool_result_chars=4096, model="m",
    )
    monkeypatch.setattr(
        "pythinker.agent.skills.SkillsLoader.build_skills_summary",
        lambda self, exclude=None: "- **memory** — manage long-term memory",
    )

    coder_prompt = sm._build_subagent_prompt(role="coder")
    explore_prompt = sm._build_subagent_prompt(role="explore")

    assert "manage long-term memory" in coder_prompt
    assert "manage long-term memory" not in explore_prompt
