"""Every AgentLoop construction path forwards Config.runtime, including SDK + one-shot CLI."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _write_config(tmp_path: Path, runtime: dict) -> Path:
    cfg = {
        "agents": {"defaults": {"workspace": str(tmp_path / "ws")}},
        "providers": {},
        "runtime": runtime,
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_pythinker_from_config_forwards_runtime_block(tmp_path):
    """SDK facade reads Config.runtime and forwards it to AgentLoop."""
    from pythinker.pythinker import Pythinker

    cfg_path = _write_config(tmp_path, {
        "policyEnabled": True,
        "policyMigrationMode": "allow-all",
        "sessionCacheMax": 64,
        "maxToolCallsPerTurn": 7,
    })

    with patch("pythinker.pythinker._make_provider") as mock_prov:
        provider = MagicMock()
        provider.get_default_model.return_value = "m"
        mock_prov.return_value = provider
        with patch("pythinker.agent.loop.ContextBuilder"), \
             patch("pythinker.agent.loop.SessionManager"), \
             patch("pythinker.agent.loop.SubagentManager"):
            api = Pythinker.from_config(config_path=cfg_path)

    assert api._loop._runtime_config.policy_enabled is True
    assert api._loop._runtime_config.policy_migration_mode == "allow-all"
    assert api._loop._runtime_config.session_cache_max == 64
    assert api._loop._runtime_config.max_tool_calls_per_turn == 7
    # And policy was constructed FROM that config (not the disabled default).
    assert api._loop.policy.enabled is True


def test_serve_session_manager_honours_session_cache_max(tmp_path, monkeypatch):
    """`pythinker serve` must build SessionManager with the configured cap.

    Regression: `serve` and `gateway` constructed `SessionManager(workspace)`
    without `cache_max`, so AgentLoop's `session_cache_max` parameter never
    reached the actual cache and long-lived API/gateway processes ignored
    the operator's bound.
    """
    cfg_path = _write_config(tmp_path, {"sessionCacheMax": 17})

    captured: dict[str, list] = {"cache_max": []}

    class _StubSessionManager:
        def __init__(self, workspace, *args, cache_max=None, **kwargs):
            captured["cache_max"].append(cache_max)
            self.workspace = workspace
            self.cache_max = cache_max

        def safe_key(self, k):
            return k

    # Both serve() and _run_gateway() function-locally
    # `from pythinker.session.manager import SessionManager`, so patching
    # the origin module is the only point both paths converge on.
    import pythinker.session.manager as sm_mod
    monkeypatch.setattr(sm_mod, "SessionManager", _StubSessionManager)

    from pythinker.cli import commands as commands_mod
    monkeypatch.setattr(commands_mod, "_make_provider", lambda _cfg: MagicMock())
    # Don't actually run AgentLoop wiring — stub it.
    import pythinker.agent.loop as loop_mod
    monkeypatch.setattr(loop_mod, "AgentLoop", lambda **_kw: MagicMock())
    # Both `create_app` and `web.run_app` are imported function-locally
    # inside serve(); patch them at their origin modules.
    import pythinker.api.server as api_server
    monkeypatch.setattr(api_server, "create_app", lambda *_a, **_kw: MagicMock(
        on_startup=[], on_cleanup=[]
    ))
    from aiohttp import web as aiohttp_web
    monkeypatch.setattr(aiohttp_web, "run_app", lambda *_a, **_kw: None)

    commands_mod.serve(host=None, port=None, workspace=None, timeout=None,
                      verbose=False, config=str(cfg_path))

    assert captured["cache_max"] == [17]


def test_build_policy_forwards_blocked_senders(tmp_path):
    """RuntimeConfig.blocked_senders must reach PolicyService at startup."""
    from pythinker.config.schema import Config, RuntimeConfig
    from pythinker.runtime._bootstrap import build_policy

    cfg = Config(runtime=RuntimeConfig(
        policy_enabled=True,
        policy_migration_mode="allow-all",
        blocked_senders=["slack:U_BAD", "telegram:42"],
    ))
    policy = build_policy(cfg)
    assert policy._blocked == {"slack:U_BAD", "telegram:42"}


def test_runtime_config_blocked_senders_round_trip_via_camel_case(tmp_path):
    """Operators set blockedSenders on disk; Pydantic must round-trip it."""
    from pythinker.config.loader import load_config

    cfg_path = _write_config(tmp_path, {
        "policyEnabled": True,
        "policyMigrationMode": "allow-all",
        "blockedSenders": ["slack:U_BAD"],
    })
    cfg = load_config(cfg_path)
    assert cfg.runtime.blocked_senders == ["slack:U_BAD"]
