import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pythinker.admin.service import AdminService
from pythinker.channels.websocket import WebSocketChannel, WebSocketConfig
from pythinker.config.loader import save_config
from pythinker.config.schema import Config
from pythinker.session.manager import Session, SessionManager


def _request(token: str | None = "tok") -> MagicMock:
    req = MagicMock()
    req.path = "/api/admin/config"
    req.headers = {"Authorization": f"Bearer {token}"} if token else {}
    return req


def _channel(tmp_path: Path, config: Config | None = None) -> tuple[WebSocketChannel, Config]:
    cfg = config or Config()
    cfg.agents.defaults.workspace = str(tmp_path / "workspace")
    cfg.providers.openai.api_key = "sk-live"
    config_path = tmp_path / "config.json"
    save_config(cfg, config_path)
    sm = SessionManager(cfg.workspace_path)
    ws_session = Session(key="websocket:browser")
    ws_session.add_message("user", "hello")
    sm.save(ws_session)
    slack_session = Session(key="slack:C123")
    slack_session.add_message("user", "from slack")
    sm.save(slack_session)
    loop = SimpleNamespace(
        _start_time=1000.0,
        _last_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        model=cfg.agents.defaults.model,
        workspace=cfg.workspace_path,
    )
    service = AdminService(
        config=cfg,
        config_path=config_path,
        session_manager=sm,
        agent_loop=loop,
    )
    ch = WebSocketChannel(
        WebSocketConfig(enabled=True, host="127.0.0.1", port=8765),
        bus=MagicMock(),
        session_manager=sm,
        agent_defaults=cfg.agents.defaults,
        admin_service=service,
    )
    ch._api_tokens["tok"] = float("inf")
    return ch, cfg


def test_admin_config_route_requires_token(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)

    response = channel._handle_admin_config(_request(token=None))

    assert response.status_code == 401


def test_admin_config_route_redacts_secrets(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)

    response = channel._handle_admin_config(_request())

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["config"]["providers"]["openai"]["apiKey"] == "********"
    assert "providers.openai.api_key" in body["secret_paths"]


def test_admin_config_schema_route_returns_schema_and_canonical_paths(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)

    response = channel._handle_admin_config_schema(_request())

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["schema"]["type"] == "object"
    assert "providers.openai.api_key" in body["secret_paths"]
    assert body["restart_required_paths"] == ["*"]


def test_admin_config_route_includes_env_refs_and_field_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_TEST_OPENAI_KEY", "sk-expanded")
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.config_path.write_text(
        json.dumps({"providers": {"openai": {"apiKey": "${ADMIN_TEST_OPENAI_KEY}"}}})
    )

    response = channel._handle_admin_config(_request())

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["config"]["providers"]["openai"]["apiKey"] == "********"
    assert body["env_references"] == {
        "providers.openai.api_key": {"env_var": "ADMIN_TEST_OPENAI_KEY", "is_secret": True}
    }
    assert body["field_defaults"]["tools.web.browser.enable"] is False
    assert "sk-expanded" not in json.dumps(body["env_references"])


def test_admin_sessions_include_all_channels_without_paths(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)

    response = channel._handle_admin_sessions(_request())

    assert response.status_code == 200
    body = json.loads(response.body)
    keys = {row["key"] for row in body["sessions"]}
    assert keys == {"websocket:browser", "slack:C123"}
    assert all("path" not in row for row in body["sessions"])
    assert all("usage" in row for row in body["sessions"])


def test_admin_surfaces_route_returns_full_control_console_snapshot(tmp_path: Path) -> None:
    cfg = Config.model_validate(
        {"channels": {"telegram": {"enabled": True, "botToken": "telegram-token"}}}
    )
    channel, _ = _channel(tmp_path, cfg)

    response = channel._handle_admin_surfaces(_request())

    assert response.status_code == 200
    body = json.loads(response.body)
    assert {"overview", "channels", "agents", "skills", "cron", "dreams", "debug", "logs"} <= set(body)
    assert body["channels"]["total"] >= 0
    assert body["skills"]["total"] >= 1
    assert body["infrastructure"]["workspace"] == str(channel._admin_service.config.workspace_path)
    assert body["agents"]["routing"]["model"]
    assert body["agents"]["routing"]["match_phase"]
    assert body["runtime"]["policy_enabled"] == body["agents"]["policy_enabled"]
    assert body["runtime"]["manifests_dir"] == body["agents"]["manifests_dir"]
    assert body["providers"]["rows"]
    assert {"name", "backend", "configured", "key_set", "active"} <= set(
        body["providers"]["rows"][0]
    )
    assert "required_secrets" in body["channels"]["rows"][0]
    assert "tools" in body
    assert "runtime" in body


def test_admin_channel_rows_include_sixty_uptime_buckets(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.channel_manager = SimpleNamespace(
        get_status=lambda: {"websocket": {"enabled": True, "running": True}}
    )

    body = service.channels()

    row = body["rows"][0]
    assert len(row["uptime_buckets"]) == 60
    assert all(isinstance(value, (int, float)) for value in row["uptime_buckets"])


async def test_admin_config_set_rpc_saves_config_and_backup(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "admin_config_set",
            "request_id": "req-1",
            "path": "logging.level",
            "value": "DEBUG",
        },
    )

    service = channel._admin_service
    assert service is not None
    assert json.loads(service.config_path.read_text())["logging"]["level"] == "DEBUG"
    assert list(service.config_path.parent.glob("config.json.bak.*"))
    channel._send_event.assert_awaited_with(
        connection,
        "admin_config_saved",
        request_id="req-1",
        path="logging.level",
        restart_required=True,
    )


async def test_admin_config_rpc_returns_validation_errors(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "admin_config_set",
            "request_id": "req-2",
            "path": "logging.level",
            "value": "LOUD",
        },
    )

    assert channel._send_event.await_args is not None
    args, kwargs = channel._send_event.await_args
    assert args[:2] == (connection, "admin_config_error")
    assert kwargs["request_id"] == "req-2"
    assert "Input should be" in kwargs["detail"]


async def test_admin_config_rpc_rejects_non_admin_connections(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    connection = MagicMock()
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "admin_config_set",
            "request_id": "req-3",
            "path": "logging.level",
            "value": "DEBUG",
        },
    )

    channel._send_event.assert_awaited_with(
        connection,
        "admin_config_error",
        request_id="req-3",
        detail="admin token required",
    )


async def test_admin_config_write_preserves_env_var_secrets_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_TEST_OPENAI_KEY", "sk-expanded")
    runtime = Config.model_validate(
        {
            "providers": {"openai": {"apiKey": "sk-expanded"}},
            "logging": {"level": "INFO"},
        }
    )
    channel, _ = _channel(tmp_path, runtime)
    service = channel._admin_service
    assert service is not None
    raw_path = service.config_path
    raw_path.write_text(
        json.dumps(
            {
                "providers": {"openai": {"apiKey": "${ADMIN_TEST_OPENAI_KEY}"}},
                "logging": {"level": "INFO"},
            }
        )
    )
    service.config_path = raw_path
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "admin_config_set",
            "request_id": "req-4",
            "path": "logging.level",
            "value": "DEBUG",
        },
    )

    on_disk = json.loads(raw_path.read_text())
    assert on_disk["providers"]["openai"]["apiKey"] == "${ADMIN_TEST_OPENAI_KEY}"
    assert on_disk["logging"]["level"] == "DEBUG"


def test_admin_config_backups_route_lists_safe_backup_payloads(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.set_config("logging.level", "DEBUG")

    response = channel._handle_admin_config_backups(_request())

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["backups"]
    backup = body["backups"][0]
    assert {"id", "mtime_ms", "size_bytes", "source", "kind", "summary"} <= set(backup)
    assert "/" not in backup["id"]
    assert "path" not in backup


async def test_admin_config_restore_backup_rpc_restores_config(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.set_config("logging.level", "DEBUG")
    backup_id = service.config_backups()[0]["id"]
    service.set_config("logging.level", "ERROR")
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "admin_config_restore_backup",
            "request_id": "restore-1",
            "backup_id": backup_id,
        },
    )

    assert json.loads(service.config_path.read_text())["logging"]["level"] == "INFO"
    channel._send_event.assert_awaited_with(
        connection,
        "admin_config_saved",
        request_id="restore-1",
        path="config.backup",
        restart_required=True,
    )


async def test_admin_test_bind_rpc_returns_probe_result(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.test_bind = AsyncMock(return_value={"ok": True})
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={
            "type": "admin_test_bind",
            "request_id": "bind-1",
            "host": "127.0.0.1",
            "port": 43210,
        },
    )

    service.test_bind.assert_awaited_once_with("127.0.0.1", 43210)
    channel._send_event.assert_awaited_with(
        connection,
        "admin_test_bind_result",
        request_id="bind-1",
        result={"ok": True},
    )


async def test_admin_test_bind_rpc_rate_limits_per_admin_connection(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.test_bind = AsyncMock(return_value={"ok": True})
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    for i in range(6):
        await channel._dispatch_envelope(
            connection,
            client_id="client-x",
            envelope={
                "type": "admin_test_bind",
                "request_id": f"bind-{i}",
                "host": "127.0.0.1",
                "port": 43210,
            },
        )

    assert service.test_bind.await_count == 5
    channel._send_event.assert_awaited_with(
        connection,
        "admin_test_bind_result",
        request_id="bind-5",
        result={"ok": False, "errno": "ERATELIMIT", "message": "Bind test rate limit exceeded"},
    )


async def test_admin_test_channel_rpc_returns_stable_check_order(tmp_path: Path) -> None:
    cfg = Config.model_validate(
        {"channels": {"telegram": {"enabled": True, "botToken": "telegram-token"}}}
    )
    channel, _ = _channel(tmp_path, cfg)
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "admin_test_channel", "request_id": "chan-1", "name": "telegram"},
    )

    assert channel._send_event.await_args is not None
    args, kwargs = channel._send_event.await_args
    assert args[:2] == (connection, "admin_test_channel_result")
    payload = kwargs["result"]
    assert payload["checks"] == [
        "channel_known",
        "config_present",
        "config_shape_valid",
        "enabled_flag_valid",
        "required_secrets_present",
        "allow_from_posture",
        "local_dependencies_present",
    ]
    assert payload["ok"] is True


async def test_admin_mcp_probe_rpc_redacts_configured_server(tmp_path: Path) -> None:
    cfg = Config.model_validate(
        {"tools": {"mcpServers": {"local": {"command": "python", "args": ["-m", "server"]}}}}
    )
    channel, _ = _channel(tmp_path, cfg)
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "admin_mcp_probe", "request_id": "mcp-1", "server": "local"},
    )

    channel._send_event.assert_awaited_with(
        connection,
        "admin_mcp_probe_result",
        request_id="mcp-1",
        result={"ok": True, "tools": [], "elapsed_ms": 0},
    )


def _admin_request(
    path: str,
    *,
    token: str | None = "tok",
    csrf: bool = True,
) -> MagicMock:
    req = MagicMock()
    req.path = path
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if csrf:
        headers["X-Pythinker-Admin-Action"] = "1"
    req.headers = headers
    return req


async def test_admin_session_stop_requires_token(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    response = await channel._handle_admin_session_stop(
        _admin_request("/api/admin/sessions/k/stop", token=None), "k"
    )
    assert response.status_code == 401


async def test_admin_session_stop_requires_csrf_header(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    response = await channel._handle_admin_session_stop(
        _admin_request("/api/admin/sessions/k/stop", csrf=False), "k"
    )
    assert response.status_code == 403


async def test_admin_session_stop_cancels_and_returns_count(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service.agent_loop._cancel_active_tasks = AsyncMock(return_value=3)

    response = await channel._handle_admin_session_stop(
        _admin_request("/api/admin/sessions/key/stop"), "key"
    )
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body == {"cancelled": 3}
    service.agent_loop._cancel_active_tasks.assert_awaited_once_with("key")


async def test_admin_session_restart_clears_checkpoint_when_session_exists(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    loop = service.agent_loop
    loop._cancel_active_tasks = AsyncMock(return_value=1)
    loop._clear_runtime_checkpoint = MagicMock()
    loop._clear_pending_user_turn = MagicMock()

    response = await channel._handle_admin_session_restart(
        _admin_request("/api/admin/sessions/websocket:browser/restart"), "websocket:browser"
    )
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["cancelled"] == 1
    assert body["found"] is True
    assert body["checkpoint_cleared"] is True
    loop._clear_runtime_checkpoint.assert_called_once()
    loop._clear_pending_user_turn.assert_called_once()


async def test_admin_session_restart_does_not_create_session_for_unknown_key(tmp_path: Path) -> None:
    channel, cfg = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    loop = service.agent_loop
    loop._cancel_active_tasks = AsyncMock(return_value=0)
    loop._clear_runtime_checkpoint = MagicMock()
    loop._clear_pending_user_turn = MagicMock()

    response = await channel._handle_admin_session_restart(
        _admin_request("/api/admin/sessions/missing/restart"), "missing"
    )
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body == {"cancelled": 0, "checkpoint_cleared": False, "found": False}
    loop._clear_runtime_checkpoint.assert_not_called()
    sessions_dir = cfg.workspace_path / "sessions"
    if sessions_dir.exists():
        assert not (sessions_dir / "missing.jsonl").exists()


async def test_admin_subagent_cancel_dispatches_to_manager(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    sub_mgr = MagicMock()
    sub_mgr.cancel_task = AsyncMock(return_value=True)
    service.agent_loop.subagents = sub_mgr

    response = await channel._handle_admin_subagent_cancel(
        _admin_request("/api/admin/subagents/abc/cancel"), "abc"
    )
    assert response.status_code == 200
    assert json.loads(response.body) == {"cancelled": True}
    sub_mgr.cancel_task.assert_awaited_once_with("abc")


async def test_admin_subagent_cancel_returns_false_when_unknown(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    sub_mgr = MagicMock()
    sub_mgr.cancel_task = AsyncMock(return_value=False)
    service.agent_loop.subagents = sub_mgr

    response = await channel._handle_admin_subagent_cancel(
        _admin_request("/api/admin/subagents/nope/cancel"), "nope"
    )
    assert response.status_code == 200
    assert json.loads(response.body) == {"cancelled": False}


def test_admin_agents_surface_includes_live_sessions(tmp_path: Path) -> None:
    """`agents().live` reports in-flight turns and subagent statuses, with stale empty keys filtered."""
    import time as _time

    from pythinker.agent.subagent import SubagentStatus

    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None

    # Stub a subagent manager + active-task map on the loop.
    sub_mgr = MagicMock()
    sub_mgr.list_statuses.return_value = [
        {
            "task_id": "abc",
            "label": "research",
            "task_description": "look up X",
            "started_at_iso": "2026-05-03T00:00:00+00:00",
            "elapsed_s": 12.5,
            "phase": "awaiting_tools",
            "iteration": 1,
            "tool_events": [],
            "usage": {},
            "stop_reason": None,
            "error": None,
            "session_key": "websocket:browser",
        },
    ]
    service.agent_loop.subagents = sub_mgr

    not_done = MagicMock()
    not_done.done.return_value = False
    done = MagicMock()
    done.done.return_value = True
    service.agent_loop._active_tasks = {
        "websocket:browser": [not_done],
        "stale:empty": [],            # stale empty key — must be filtered
        "stale:done": [done],         # done-only — must be filtered
        "": [not_done],               # empty key — must be skipped
    }

    out = service.agents()
    sessions = out["live"]["sessions"]
    keys = {s["key"] for s in sessions}
    assert keys == {"websocket:browser"}
    row = next(s for s in sessions if s["key"] == "websocket:browser")
    assert row["in_flight"] == 1
    assert row["subagent_count"] == 1
    assert row["subagents"][0]["task_id"] == "abc"
    # The status dataclass shape we plug in mirrors what list_statuses produces:
    _ = SubagentStatus  # ensure import path stays live
    _ = _time  # silence unused import for ruff


async def test_admin_browser_probe_rpc_redacts_runtime_state(tmp_path: Path) -> None:
    channel, _ = _channel(tmp_path)
    service = channel._admin_service
    assert service is not None
    service._browser_status_provider = lambda: SimpleNamespace(
        active_contexts=2,
        last_url="https://example.com/private?token=secret#frag",
        cookie_size_bytes=123,
    )
    connection = MagicMock()
    channel._admin_connections.add(connection)
    channel._send_event = AsyncMock()

    await channel._dispatch_envelope(
        connection,
        client_id="client-x",
        envelope={"type": "admin_browser_probe", "request_id": "browser-1"},
    )

    channel._send_event.assert_awaited_with(
        connection,
        "admin_browser_probe_result",
        request_id="browser-1",
        result={
            "active_contexts": 2,
            "last_url": "https://example.com/private",
            "cookie_size_bytes": 123,
        },
    )
