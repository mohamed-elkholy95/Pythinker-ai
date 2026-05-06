"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

import os
from pathlib import Path

from pythinker.config.loader import get_config_path
from pythinker.utils.helpers import ensure_dir

_DEFAULT_AGENT_ID = "default"


def current_agent_id() -> str:
    """Return the currently active agent id.

    Resolution order:
      1. ``$PYTHINKER_AGENT_ID`` env var (non-empty wins).
      2. The single-line ``~/.pythinker/current-agent`` marker file, if it exists
         and is readable.
      3. ``"default"``.

    A bad / unreadable marker file falls through silently — not the kind of
    thing that should refuse to load the wizard.
    """
    env_value = os.environ.get("PYTHINKER_AGENT_ID", "").strip()
    if env_value:
        return env_value
    marker = Path.home() / ".pythinker" / "current-agent"
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return _DEFAULT_AGENT_ID
    return text or _DEFAULT_AGENT_ID


def agent_dir(agent_id: str) -> Path:
    """Return ``~/.pythinker/agents/<id>/`` as a Path. Does not create it."""
    return Path.home() / ".pythinker" / "agents" / agent_id


def agent_config_path(agent_id: str) -> Path:
    """Return the config-file path for ``agent_id``, with legacy fallback.

    If ``~/.pythinker/agents/<id>/`` exists, returns ``<that>/config.json``.
    Otherwise falls back to the legacy single-config path ``~/.pythinker/config.json``
    so existing single-agent installs keep working unchanged.
    """
    candidate = agent_dir(agent_id) / "config.json"
    if candidate.parent.is_dir():
        return candidate
    return Path.home() / ".pythinker" / "config.json"


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".pythinker" / "workspace"
    return ensure_dir(path)


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to pythinker's default workspace path."""
    current = Path(workspace).expanduser() if workspace is not None else Path.home() / ".pythinker" / "workspace"
    default = Path.home() / ".pythinker" / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    return Path.home() / ".pythinker" / "history" / "cli_history"


def get_bridge_install_dir() -> Path:
    """Return the shared WhatsApp bridge installation directory."""
    return Path.home() / ".pythinker" / "bridge"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback."""
    return Path.home() / ".pythinker" / "sessions"


def get_browser_storage_dir() -> Path:
    """Directory holding per-session ``storage_state.json`` files for the browser tool."""
    return get_runtime_subdir("browser")


def get_update_dir() -> Path:
    """Directory holding the update-check cache and the update lock file."""
    return get_runtime_subdir("update")
