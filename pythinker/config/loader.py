"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path
from typing import Any

import pydantic
from loguru import logger
from pydantic import BaseModel

from pythinker.config.schema import Config, WebSearchProviderConfig

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".pythinker" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    _apply_ssrf_whitelist(config)

    if migrate_websearch_legacy_keys(config):
        try:
            save_config(config, path)
        except OSError as exc:
            logger.warning(
                "Migrated config in memory but could not persist to disk: {}", exc
            )

    return config


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from pythinker.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_API_KEY_PROVIDERS: frozenset[str] = frozenset({"brave", "tavily", "jina", "kagi"})
_BASE_URL_PROVIDERS: frozenset[str] = frozenset({"searxng"})


def migrate_websearch_legacy_keys(config: Config) -> bool:
    """Migrate tools.web.search.{api_key,base_url} into the per-provider dict.

    Idempotent: returns ``True`` only when the in-memory config was modified.
    Behaviour for stray legacy secrets on providers that do not consume them
    (``duckduckgo`` or unknown values) is to log a warning and leave the
    legacy field in place, preserving the migration's "copy never delete"
    promise.
    """
    search = config.tools.web.search
    provider = (search.provider or "").strip().lower() or "brave"
    changed = False

    legacy_api_key = search.api_key or ""
    legacy_base_url = search.base_url or ""

    if legacy_api_key:
        if provider in _API_KEY_PROVIDERS:
            slot = search.providers.get(provider) or WebSearchProviderConfig()
            if not slot.api_key:
                slot.api_key = legacy_api_key
            search.providers[provider] = slot
            search.api_key = ""
            changed = True
        else:
            logger.warning(
                "tools.web.search.apiKey is set but provider {!r} does not consume it; "
                "ignoring. Move it under tools.web.search.providers if intended.",
                provider,
            )

    if legacy_base_url:
        if provider in _BASE_URL_PROVIDERS:
            slot = search.providers.get(provider) or WebSearchProviderConfig()
            if not slot.base_url:
                slot.base_url = legacy_base_url
            search.providers[provider] = slot
            search.base_url = ""
            changed = True
        else:
            logger.warning(
                "tools.web.search.baseUrl is set but provider {!r} does not consume it; "
                "ignoring. Move it under tools.web.search.providers if intended.",
                provider,
            )

    if changed:
        logger.info("Migrated tools.web.search to per-provider key storage.")
    return changed


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` (e.g.
    ``DreamConfig.cron``) survive; returns the same instance when no
    references are present. Raises ``ValueError`` if a referenced
    variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return resolved if any(nv is not ov for nv, ov in zip(resolved, obj)) else obj
    return obj


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ``${VAR}`` patterns in plain strings/dicts/lists."""
    if isinstance(obj, str):
        return _ENV_REF_PATTERN.sub(_env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Move tools.myEnabled / tools.mySet → tools.my.{enable, allowSet}.
    # The old flat keys shipped in the initial MyTool landing; wrapping them in a
    # sub-config keeps `web` / `exec` / `my` symmetric and gives room to grow.
    if "myEnabled" in tools or "mySet" in tools:
        my_cfg = tools.setdefault("my", {})
        if "myEnabled" in tools and "enable" not in my_cfg:
            my_cfg["enable"] = tools.pop("myEnabled")
        else:
            tools.pop("myEnabled", None)
        if "mySet" in tools and "allowSet" not in my_cfg:
            my_cfg["allowSet"] = tools.pop("mySet")
        else:
            tools.pop("mySet", None)

    return data
