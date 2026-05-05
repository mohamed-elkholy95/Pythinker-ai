"""Snapshot helpers for the admin dashboard.

Pure builders that turn a :class:`~pythinker.config.schema.Config` (and
related state) into JSON-friendly dictionaries for the WebUI admin
surfaces. Extracted from ``pythinker/admin/service.py`` per
``.agents/plans/2026-05-04-simplification-alignment.md`` §A4.
"""

from __future__ import annotations

from typing import Any

from pythinker.admin.redact import _redacted_path_is_set
from pythinker.config.editing import ConfigBackupEntry
from pythinker.config.schema import Config


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


def _backup_to_payload(entry: ConfigBackupEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "mtime_ms": entry.mtime_ms,
        "size_bytes": entry.size_bytes,
        "source": entry.source,
        "kind": entry.kind,
        "summary": entry.summary,
    }
