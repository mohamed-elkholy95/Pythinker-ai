"""Read and mutate local-admin dashboard state."""

from __future__ import annotations

import asyncio
import errno
import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pythinker import __version__
from pythinker.agent.usage import estimate_session_usage
from pythinker.config.editing import (
    ConfigBackupEntry,
    ConfigEditError,
    collect_env_references,
    collect_field_defaults,
    list_config_backups,
    read_config_value,
    redacted_config,
    restore_config_backup,
    save_config_with_backup,
    set_config_value,
    unset_config_value,
)
from pythinker.config.loader import load_config
from pythinker.config.schema import Config


def _path_value(payload: dict[str, Any], path: str) -> Any:
    from pydantic.alias_generators import to_camel

    current: Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        if segment in current:
            current = current[segment]
            continue
        alias = to_camel(segment)
        if alias in current:
            current = current[alias]
            continue
        return None
    return current


def _redacted_path_is_set(payload: dict[str, Any], path: str) -> bool:
    value = _path_value(payload, path)
    return value not in (None, "", [], {})


def _provider_routing(config: Config) -> dict[str, object]:
    return config._trace_match_provider(config.agents.defaults.model)


def _provider_rows(config: Config) -> list[dict[str, object]]:
    from pythinker.providers.registry import PROVIDERS

    active_provider = config.get_provider_name(config.agents.defaults.model)
    rows: list[dict[str, object]] = []
    for spec in PROVIDERS:
        provider_config = getattr(config.providers, spec.name, None)
        api_key = getattr(provider_config, "api_key", None) if provider_config else None
        api_base = getattr(provider_config, "api_base", None) if provider_config else None
        rows.append(
            {
                "name": spec.name,
                "backend": spec.backend,
                "is_oauth": spec.is_oauth,
                "is_local": spec.is_local,
                "is_gateway": spec.is_gateway,
                "is_direct": spec.is_direct,
                "configured": provider_config is not None,
                "key_set": bool(api_key),
                "api_base": api_base or spec.default_api_base or None,
                "active": spec.name == active_provider,
            }
        )
    return rows


def _required_secret_status(
    metadata: dict[str, object] | None,
    config_payload: dict[str, Any],
) -> list[dict[str, object]]:
    required = metadata.get("required_secrets", []) if metadata else []
    if not isinstance(required, list):
        return []
    return [
        {"path": path, "set": _redacted_path_is_set(config_payload, path)}
        for path in required
        if isinstance(path, str)
    ]


def _tools_surface(config: Config) -> dict[str, object]:
    return {
        "web": {
            "browser": {
                "pool_size": 0,
                "headless": config.tools.web.browser.headless,
                "profile_dir": config.tools.web.browser.storage_state_dir,
                "active_contexts": 0,
            },
            "search": {
                "active_provider": config.tools.web.search.provider,
                "providers": sorted(config.tools.web.search.providers),
            },
        },
        "exec": {
            "sandbox": {
                "backend": config.tools.exec.sandbox,
                "available": False,
                "fallback": None,
            }
        },
        "mcp_servers": [
            {
                "name": name,
                "transport": server.type,
                "command": server.command,
                "env_keys": len(server.env),
            }
            for name, server in sorted(config.tools.mcp_servers.items())
        ],
        "ssrf": {
            "whitelist": list(config.tools.ssrf_whitelist),
            "blocked_categories": ["rfc1918", "loopback", "link-local", "cgn", "ula"],
        },
    }


def _runtime_surface(config: Config) -> dict[str, object]:
    return {
        "policy_enabled": config.runtime.policy_enabled,
        "manifests_dir": config.runtime.manifests_dir,
        "telemetry_sink": config.runtime.telemetry_sink,
        "session_cache_max": config.runtime.session_cache_max,
    }


def _coarse_errno(value: int | None) -> str:
    if value is None:
        return "EUNKNOWN"
    mapping = {
        errno.EADDRINUSE: "EADDRINUSE",
        errno.EACCES: "EACCES",
        errno.EADDRNOTAVAIL: "EADDRNOTAVAIL",
    }
    return mapping.get(value, "EUNKNOWN")


def _redact_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _backup_to_payload(entry: ConfigBackupEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "mtime_ms": entry.mtime_ms,
        "size_bytes": entry.size_bytes,
        "source": entry.source,
        "kind": entry.kind,
        "summary": entry.summary,
    }


def _configured_channel_section(config: Config, name: str) -> Any:
    configured = getattr(config.channels, "__pydantic_extra__", {}) or {}
    return configured.get(name)


def _validate_channel_config_only(config: Config, name: str) -> dict[str, object]:
    started = time.perf_counter()
    checks = [
        "channel_known",
        "config_present",
        "config_shape_valid",
        "enabled_flag_valid",
        "required_secrets_present",
        "allow_from_posture",
        "local_dependencies_present",
    ]
    from pythinker.channels.registry import get_channel_config_fields

    metadata = get_channel_config_fields(name)
    section = _configured_channel_section(config, name)
    redacted = redacted_config(config)["config"]
    error: str | None = None
    if metadata is None:
        error = "unknown_channel"
    elif section is None:
        error = "missing_config"
    elif not isinstance(section, dict):
        error = "invalid_config_shape"
    elif not isinstance(section.get("enabled", False), bool):
        error = "invalid_enabled_flag"
    else:
        required = metadata.get("required_secrets", [])
        secret_paths = required if isinstance(required, list) else []
        missing = [
            path
            for path in secret_paths
            if isinstance(path, str) and not _redacted_path_is_set(redacted, path)
        ]
        if missing:
            error = "missing_required_secrets"
        elif "allow_from" in section and not isinstance(section.get("allow_from"), list):
            error = "invalid_allow_from"
    return {
        "ok": error is None,
        "checks": checks,
        "last_error": error,
        "ms": int((time.perf_counter() - started) * 1000),
    }


_FIELD_DEFAULTS_CACHE: dict[str, object] | None = None


def _config_field_defaults() -> dict[str, object]:
    global _FIELD_DEFAULTS_CACHE
    if _FIELD_DEFAULTS_CACHE is None:
        _FIELD_DEFAULTS_CACHE = collect_field_defaults(Config.model_json_schema(by_alias=True))
    return dict(_FIELD_DEFAULTS_CACHE)


class AdminService:
    """Small domain layer behind the embedded WebUI admin dashboard."""

    def __init__(
        self,
        *,
        config: Config,
        config_path: Path,
        session_manager: Any | None = None,
        agent_loop: Any | None = None,
        channel_manager: Any | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.session_manager = session_manager
        self.agent_loop = agent_loop
        self.channel_manager = channel_manager
        self._browser_status_provider: Callable[[], object | None] | None = None

    def overview(self) -> dict[str, Any]:
        start_time = float(getattr(self.agent_loop, "_start_time", time.time()))
        channels = self.channel_manager.channels if self.channel_manager is not None else {}
        return {
            "version": __version__,
            "uptime_s": max(0, int(time.time() - start_time)),
            "workspace": str(self.config.workspace_path),
            "config_path": str(self.config_path),
            "gateway": {
                "host": self.config.gateway.host,
                "port": self.config.gateway.port,
            },
            "api": {
                "host": self.config.api.host,
                "port": self.config.api.port,
            },
            "websocket": self._websocket_config(),
            "agent": {
                "provider": self.config.get_provider_name(),
                "model": getattr(self.agent_loop, "model", self.config.agents.defaults.model),
                "configured_model": self.config.agents.defaults.model,
            },
            "channels": [
                {"name": name, "enabled": True}
                for name in sorted(channels)
            ],
            "local_admin": True,
        }

    def _websocket_config(self) -> dict[str, Any]:
        section = getattr(self.config.channels, "websocket", None)
        if section is None:
            section = (getattr(self.config.channels, "__pydantic_extra__", {}) or {}).get(
                "websocket", {}
            )
        if isinstance(section, dict):
            return {
                "host": section.get("host", "127.0.0.1"),
                "port": section.get("port", 8765),
                "path": section.get("path", "/ws"),
            }
        return {
            "host": getattr(section, "host", "127.0.0.1"),
            "port": getattr(section, "port", 8765),
            "path": getattr(section, "path", "/ws"),
        }

    def sessions(self) -> dict[str, Any]:
        if self.session_manager is None:
            return {"sessions": []}
        rows = []
        for item in self.session_manager.list_sessions():
            key = item.get("key", "")
            channel, _, chat_id = key.partition(":")
            cleaned = {k: v for k, v in item.items() if k != "path"}
            cleaned["channel"] = channel
            cleaned["chat_id"] = chat_id
            cleaned["usage"] = self._session_usage(key)
            rows.append(cleaned)
        return {"sessions": rows}

    def _session_usage(self, key: str) -> dict[str, int]:
        if self.session_manager is None:
            return {"used": 0, "limit": self.config.agents.defaults.context_window_tokens}
        data = self.session_manager.read_session_file(key)
        if not isinstance(data, dict):
            return {"used": 0, "limit": self.config.agents.defaults.context_window_tokens}

        class _SessionView:
            messages = data.get("messages", [])

        return estimate_session_usage(_SessionView(), self.config.agents.defaults)  # type: ignore[arg-type]

    def models(self) -> dict[str, Any]:
        provider = self.config.get_provider_name()
        default = self.config.agents.defaults.model
        rows: list[dict[str, Any]] = [{"name": default, "source": "configured", "active": True}]
        seen = {default}
        for model in self.config.agents.defaults.alternate_models:
            if model and model not in seen:
                rows.append({"name": model, "source": "alternate", "active": False})
                seen.add(model)
        try:
            from pythinker.cli.models import RECOMMENDED_BY_PROVIDER

            for model in RECOMMENDED_BY_PROVIDER.get(provider, ()):
                if model not in seen:
                    rows.append({"name": model, "source": "recommended", "active": False})
                    seen.add(model)
        except Exception:
            pass
        return {
            "provider": provider,
            "active_model": getattr(self.agent_loop, "model", default),
            "models": rows,
        }

    def usage(self) -> dict[str, Any]:
        from pythinker.agent.usage_ledger import load_usage_summary

        last_usage = getattr(self.agent_loop, "_last_usage", {}) or {}
        ledger = load_usage_summary(self.config.workspace_path)
        return {
            "last_turn": dict(last_usage),
            "sessions": self.sessions()["sessions"],
            "consumption": {
                "total_tokens": int(ledger.get("total_tokens", 0)),
                "cost": None,
                "currency": None,
            },
            "ledger": ledger,
        }

    def surfaces(self) -> dict[str, Any]:
        surfaces = {
            "overview": self.overview(),
            "channels": self.channels(),
            "sessions": self.sessions(),
            "usage": self.usage(),
            "models": self.models(),
            "agents": self.agents(),
            "skills": self.skills(),
            "cron": self.cron(),
            "dreams": self.dreams(),
            "config": self.config_payload(),
            "appearance": self.appearance(),
            "infrastructure": self.infrastructure(),
            "debug": self.debug(),
            "logs": self.logs(),
        }
        redacted_config = surfaces["config"]["config"]
        surfaces["agents"]["routing"] = _provider_routing(self.config)
        surfaces["providers"] = {"rows": _provider_rows(self.config)}
        surfaces["tools"] = _tools_surface(self.config)
        surfaces["runtime"] = _runtime_surface(self.config)

        from pythinker.channels.registry import get_channel_config_fields

        for row in surfaces["channels"]["rows"]:
            metadata = get_channel_config_fields(row["name"])
            row["required_secrets"] = _required_secret_status(metadata, redacted_config)
            row["config_fields"] = metadata
            row.setdefault("last_error", None)
        return surfaces

    def channels(self) -> dict[str, Any]:
        status = {}
        if self.channel_manager is not None and hasattr(self.channel_manager, "get_status"):
            try:
                status = self.channel_manager.get_status()
            except Exception:
                status = {}
        configured = getattr(self.config.channels, "__pydantic_extra__", {}) or {}
        rows = []
        names = sorted(set(configured) | set(status))
        for name in names:
            section = configured.get(name, {})
            enabled = bool(section.get("enabled", False)) if isinstance(section, dict) else False
            runtime = status.get(name, {})
            rows.append({
                "name": name,
                "enabled": enabled or bool(runtime.get("enabled")),
                "running": bool(runtime.get("running")),
                "streaming": bool(section.get("streaming", False)) if isinstance(section, dict) else False,
                "uptime_buckets": list(runtime.get("uptime_buckets", [0] * 60)),
            })
        return {
            "total": len(rows),
            "running": sum(1 for row in rows if row["running"]),
            "rows": rows,
        }

    def agents(self) -> dict[str, Any]:
        registry = getattr(self.agent_loop, "agent_registry", None)
        manifests = []
        if registry is not None:
            for agent_id in registry.ids():
                manifest = registry.get(agent_id)
                if manifest is None:
                    continue
                manifests.append(manifest.model_dump(mode="json", by_alias=True))

        # Live runtime view: which sessions have an in-flight turn or live
        # subagents. ``_active_tasks`` is a single-underscore attribute on
        # AgentLoop (not name-mangled). Its done-callback (loop.py:1037)
        # removes finished tasks from the per-key list but never deletes the
        # key, so we must filter empty rows here.
        sub_mgr = getattr(self.agent_loop, "subagents", None)
        statuses = sub_mgr.list_statuses() if sub_mgr is not None else []
        by_session: dict[str, list[dict[str, Any]]] = {}
        for row in statuses:
            by_session.setdefault(row.get("session_key") or "", []).append(row)
        active = getattr(self.agent_loop, "_active_tasks", {}) or {}
        live_sessions: list[dict[str, Any]] = []
        for key in sorted(set(by_session) | set(active.keys())):
            if not key:
                continue
            in_flight = sum(1 for t in active.get(key, []) if not t.done())
            subs = by_session.get(key, [])
            if in_flight == 0 and not subs:
                continue
            live_sessions.append({
                "key": key,
                "in_flight": in_flight,
                "subagent_count": len(subs),
                "subagents": subs,
            })

        return {
            "default_agent_id": self.config.runtime.default_agent_id,
            "policy_enabled": self.config.runtime.policy_enabled,
            "manifests_dir": self.config.runtime.manifests_dir,
            "total": len(manifests),
            "agents": manifests,
            "live": {"sessions": live_sessions},
        }

    def skills(self) -> dict[str, Any]:
        from pythinker.agent.skills import SkillsLoader

        loader = SkillsLoader(
            self.config.workspace_path,
            disabled_skills=set(self.config.agents.defaults.disabled_skills),
        )
        rows = []
        for entry in loader.list_skills(filter_unavailable=False):
            meta = loader.get_skill_metadata(entry["name"]) or {}
            rows.append({
                "name": entry["name"],
                "source": entry["source"],
                "description": meta.get("description") or entry["name"],
                "always": bool(meta.get("always")),
                "disabled": entry["name"] in self.config.agents.defaults.disabled_skills,
            })
        return {
            "total": len(rows),
            "disabled": len(self.config.agents.defaults.disabled_skills),
            "rows": rows,
        }

    def cron(self) -> dict[str, Any]:
        service = getattr(self.agent_loop, "cron_service", None)
        if service is None:
            return {"status": {"enabled": False, "jobs": 0}, "jobs": []}
        try:
            status = service.status()
            jobs = [self._cron_job_payload(job) for job in service.list_jobs(include_disabled=True)]
        except Exception as exc:
            return {"status": {"enabled": False, "jobs": 0, "error": str(exc)}, "jobs": []}
        return {"status": status, "jobs": jobs}

    def _cron_job_payload(self, job: Any) -> dict[str, Any]:
        if is_dataclass(job):
            payload = asdict(job)
        elif hasattr(job, "model_dump"):
            payload = job.model_dump(mode="json", by_alias=True)
        else:
            payload = dict(job)
        # Keep the schedule/routing visible, but avoid dumping long task bodies into overview cards.
        message = payload.get("payload", {}).get("message")
        if isinstance(message, str) and len(message) > 160:
            payload["payload"]["message"] = f"{message[:160]}..."
        return payload

    def dreams(self) -> dict[str, Any]:
        dream = self.config.agents.defaults.dream
        return {
            "schedule": dream.describe_schedule(),
            "model_override": dream.model_override,
            "max_batch_size": dream.max_batch_size,
            "max_iterations": dream.max_iterations,
            "annotate_line_ages": dream.annotate_line_ages,
        }

    def appearance(self) -> dict[str, Any]:
        return {
            "theme": "system",
            "mode": "light/dark",
            "radius": "control",
            "notes": "Appearance is currently browser-local via the WebUI theme toggle.",
        }

    def infrastructure(self) -> dict[str, Any]:
        return {
            "workspace": str(self.config.workspace_path),
            "config_path": str(self.config_path),
            "gateway": self.overview()["gateway"],
            "api": self.overview()["api"],
            "websocket": self._websocket_config(),
            "telemetry": {
                "sink": self.config.runtime.telemetry_sink,
                "jsonl_path": self.config.runtime.telemetry_jsonl_path,
            },
            "mcp_servers": len(self.config.tools.mcp_servers),
            "ssrf_whitelist": list(self.config.tools.ssrf_whitelist),
        }

    def debug(self) -> dict[str, Any]:
        bus = getattr(self.agent_loop, "bus", None)
        subagents = getattr(self.agent_loop, "subagents", None)
        return {
            "policy_enabled": self.config.runtime.policy_enabled,
            "blocked_senders": len(self.config.runtime.blocked_senders),
            "queue_depth": {
                "inbound": self._call_int(bus, "inbound_size"),
                "outbound": self._call_int(bus, "outbound_size"),
            },
            "subagents_running": self._call_int(subagents, "get_running_count"),
            "session_cache_max": self.config.runtime.session_cache_max,
        }

    def logs(self, *, limit: int = 80) -> dict[str, Any]:
        paths: list[Path] = []
        if self.config.runtime.telemetry_jsonl_path:
            paths.append(Path(self.config.runtime.telemetry_jsonl_path).expanduser())
        paths.extend(sorted((self.config_path.parent / "logs").glob("*.log"))[-3:])
        entries: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists() or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines[-limit:]:
                entries.append(self._log_entry(path, line))
        return {
            "entries": entries[-limit:],
            "sources": [str(path) for path in paths],
            "truncated": len(entries) > limit,
        }

    def _log_entry(self, path: Path, line: str) -> dict[str, Any]:
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                ts = (
                    payload.get("ts")
                    or payload.get("time")
                    or payload.get("timestamp")
                )
                return {
                    "source": str(path),
                    "ts": ts if isinstance(ts, str) else None,
                    "level": payload.get("level") or payload.get("event") or "info",
                    "message": payload.get("message") or payload.get("event") or line,
                    "raw": payload,
                }
        except json.JSONDecodeError:
            pass
        return {"source": str(path), "ts": None, "level": "log", "message": line, "raw": line}

    async def test_bind(self, host: str, port: int) -> dict[str, object]:
        allowed = {"127.0.0.1", "::1", self.config.api.host, self.config.gateway.host}
        if host not in allowed:
            return {"ok": False, "errno": "EACCES", "message": "Bind target is not allowed"}
        try:
            server = await asyncio.wait_for(
                asyncio.start_server(lambda _reader, _writer: None, host, port),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            return {"ok": False, "errno": "ETIMEDOUT", "message": "Bind test timed out"}
        except OSError as exc:
            coarse = _coarse_errno(exc.errno)
            return {"ok": False, "errno": coarse, "message": coarse}
        server.close()
        await server.wait_closed()
        return {"ok": True}

    async def test_channel(self, name: str) -> dict[str, object]:
        return _validate_channel_config_only(self.config, name)

    async def mcp_probe(self, server: str) -> dict[str, object]:
        if server not in self.config.tools.mcp_servers:
            return {"ok": False, "tools": [], "elapsed_ms": 0, "error": "unknown_server"}
        return {"ok": True, "tools": [], "elapsed_ms": 0}

    async def browser_probe(self) -> dict[str, object]:
        provider = getattr(self, "_browser_status_provider", None)
        state = provider() if callable(provider) else None
        active_contexts = getattr(state, "active_contexts", 0)
        cookie_size_bytes = getattr(state, "cookie_size_bytes", 0)
        last_url = getattr(state, "last_url", None)
        return {
            "active_contexts": active_contexts if isinstance(active_contexts, int) else 0,
            "last_url": _redact_url(last_url if isinstance(last_url, str) else None),
            "cookie_size_bytes": cookie_size_bytes if isinstance(cookie_size_bytes, int) else 0,
        }

    @staticmethod
    def _call_int(obj: Any, method: str) -> int:
        if obj is None:
            return 0
        fn = getattr(obj, method, None)
        if not callable(fn):
            return 0
        try:
            return int(fn())
        except Exception:
            return 0

    def config_payload(self) -> dict[str, Any]:
        raw = self._raw_disk_config()
        payload = redacted_config(self._disk_config())
        payload["env_references"] = collect_env_references(raw, set(payload["secret_paths"]))
        payload["field_defaults"] = _config_field_defaults()
        payload["restart_required_paths"] = ["*"]
        return payload

    def config_schema(self) -> dict[str, Any]:
        schema = Config.model_json_schema(by_alias=True)
        return {
            "schema": schema,
            "secret_paths": self.config_payload()["secret_paths"],
            "field_defaults": _config_field_defaults(),
            "restart_required_paths": ["*"],
        }

    def read_config(self, path: str) -> Any:
        return read_config_value(self._disk_config(), path)

    def set_config(self, path: str, value: Any) -> None:
        config = self._disk_config()
        set_config_value(config, path, value)
        save_config_with_backup(config, self.config_path)
        self._mirror_runtime_edit(path, value)

    def unset_config(self, path: str) -> None:
        config = self._disk_config()
        unset_config_value(config, path)
        save_config_with_backup(config, self.config_path)
        self._mirror_runtime_unset(path)

    def replace_secret(self, path: str, value: Any) -> None:
        if not isinstance(value, str) or not value:
            raise ConfigEditError("secret replacement value must be a non-empty string")
        self.set_config(path, value)

    def config_backups(self) -> list[dict[str, object]]:
        return [_backup_to_payload(entry) for entry in list_config_backups(self.config_path)]

    def restore_config_backup(self, backup_id: str) -> dict[str, object]:
        restore_config_backup(self.config_path, backup_id)
        self.config = self._disk_config()
        return {"ok": True, "restart_required": True}

    def _disk_config(self) -> Config:
        return load_config(self.config_path)

    def _raw_disk_config(self) -> dict[str, object]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _mirror_runtime_edit(self, path: str, value: Any) -> None:
        try:
            set_config_value(self.config, path, value)
        except Exception:
            pass

    def _mirror_runtime_unset(self, path: str) -> None:
        try:
            unset_config_value(self.config, path)
        except Exception:
            pass
