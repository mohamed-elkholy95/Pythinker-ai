"""AgentRegistry populates PolicyService.allowed_tools at startup; lifecycle filtering applies."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pythinker.bus.queue import MessageBus
from pythinker.config.schema import RuntimeConfig
from pythinker.runtime.manifest import AgentRegistry
from pythinker.runtime.policy import PolicyService


def _write_manifest(directory: Path, **fields):
    payload = {"id": "m", "name": "M", "version": "0.1.0", "model": "x",
               "owner": "o", "allowed_tools": [], **fields}
    (directory / f"{payload['id']}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_apply_registry_populates_allowed_tools_from_active_manifests(tmp_path):
    _write_manifest(tmp_path, id="research", allowed_tools=["read_file", "grep"], lifecycle="active")
    _write_manifest(tmp_path, id="ops", allowed_tools=["exec"], lifecycle="active")
    _write_manifest(tmp_path, id="legacy", allowed_tools=["*"], lifecycle="retired")

    reg = AgentRegistry.load_dir(tmp_path)
    pol = PolicyService(enabled=True)
    pol.apply_registry(reg)
    # Only "active" manifests are mounted. "retired" is ignored.
    assert pol.allowed_tools_for("research") == ["read_file", "grep"]
    assert pol.allowed_tools_for("ops") == ["exec"]
    assert pol.allowed_tools_for("legacy") == []


def test_apply_registry_with_empty_registry_keeps_deny_default(tmp_path):
    reg = AgentRegistry.load_dir(tmp_path)  # empty dir
    pol = PolicyService(enabled=True)
    pol.apply_registry(reg)
    # No manifests, no migration mode → deny-default still in effect.
    from pythinker.runtime.context import RequestContext
    ctx = RequestContext.for_inbound(channel="cli", sender_id="u", chat_id="c", session_key="cli:c")
    decision = pol.authorize_tool_call(ctx, "read_file")
    assert decision.allowed is False
    assert "no allow-list" in decision.reason


def test_loop_loads_registry_from_runtime_config(tmp_path):
    from pythinker.agent.loop import AgentLoop

    manifests = tmp_path / "agents"
    manifests.mkdir()
    _write_manifest(manifests, id="research", allowed_tools=["read_file"], lifecycle="active")

    rt = RuntimeConfig(policy_enabled=True, manifests_dir=str(manifests))
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            runtime_config=rt,
            policy=PolicyService(enabled=True),
        )
    assert loop.agent_registry is not None
    assert "research" in loop.agent_registry.ids()
    assert loop.policy.allowed_tools_for("research") == ["read_file"]


def test_normalize_resolves_agent_id_from_config(tmp_path):
    """With runtime.default_agent_id set in config, _normalize_context returns it.

    No attribute poking — this proves the manifest-driven configuration path
    actually reaches _normalize_context without operator intervention.
    """
    from pythinker.agent.loop import AgentLoop

    manifests = tmp_path / "agents"
    manifests.mkdir()
    _write_manifest(manifests, id="research", allowed_tools=["read_file"], lifecycle="active")
    rt = RuntimeConfig(
        policy_enabled=True,
        manifests_dir=str(manifests),
        default_agent_id="research",
    )
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            runtime_config=rt,
            policy=PolicyService(enabled=True),
        )
    assert loop.default_agent_id == "research"  # came from config, not poked
    ctx = loop._normalize_context(
        seed={"channel": "cli", "sender_id": "u", "chat_id": "c"},
        session_key="cli:c",
    )
    assert ctx.agent_id == "research"
    assert ctx.policy_version == 1


def test_draft_manifest_is_not_bound_to_request_context(tmp_path):
    """Lifecycle gate for IDENTITY (not just allow-list): a draft manifest must
    never become ctx.agent_id. Otherwise telemetry would be tagged with an
    agent that has no live policy entry — audit logs would lie."""
    from pythinker.agent.loop import AgentLoop

    manifests = tmp_path / "agents"
    manifests.mkdir()
    _write_manifest(manifests, id="research", allowed_tools=["read_file"], lifecycle="draft")
    rt = RuntimeConfig(
        policy_enabled=True,
        manifests_dir=str(manifests),
        default_agent_id="research",
    )
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            runtime_config=rt,
            policy=PolicyService(enabled=True),
        )
    # default_agent_id="research" is in the registry but lifecycle=draft,
    # so policy.active_agent_ids() does NOT contain it — identity must
    # fall back to "default".
    ctx = loop._normalize_context(
        seed={"channel": "cli", "sender_id": "u", "chat_id": "c"},
        session_key="cli:c",
    )
    assert ctx.agent_id == "default"
    # And the live policy_version must match — never hard-coded.
    assert ctx.policy_version == loop.policy.policy_version


def test_default_agent_id_falls_back_when_missing_from_registry(tmp_path):
    """Operator misconfig: default_agent_id="ghost" but only "research" exists.

    Loop must log a warning and fall back to the first active manifest,
    not silently leave operators in deny-all.
    """
    from pythinker.agent.loop import AgentLoop

    manifests = tmp_path / "agents"
    manifests.mkdir()
    _write_manifest(manifests, id="research", allowed_tools=["read_file"], lifecycle="active")
    rt = RuntimeConfig(
        policy_enabled=True,
        manifests_dir=str(manifests),
        default_agent_id="ghost",
    )
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager") as mock_sub:
        mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            runtime_config=rt,
            policy=PolicyService(enabled=True),
        )
    assert loop.default_agent_id == "research"  # fell back from "ghost"
    ctx = loop._normalize_context(
        seed={"channel": "cli", "sender_id": "u", "chat_id": "c"},
        session_key="cli:c",
    )
    assert ctx.agent_id == "research"
