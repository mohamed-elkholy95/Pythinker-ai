"""Reset-scope ladder + immediate destructive-op execution.

Per spec: typed-`reset` is the destructive-op consent. Credential / session /
full deletes happen immediately at step 4 (in `apply_immediate`). Only the
config rename is deferred to step 14 so a Skip there can revert the config swap.
"""

from __future__ import annotations

import enum
import shutil
from pathlib import Path

from loguru import logger

from pythinker.cli.onboard_views import clack
from pythinker.config.paths import get_data_dir


class ResetScope(enum.IntEnum):
    CONFIG = 1
    CREDENTIALS = 2
    SESSIONS = 3
    FULL = 4


SCOPE_OPTIONS = [
    ("config", "Config only", "Wipe ~/.pythinker/config.json (keep credentials, sessions)."),
    ("credentials", "+ credentials", "Also delete OAuth tokens (oauth_cli_kit files)."),
    ("sessions", "+ sessions", "Also delete ~/.pythinker/sessions/ (chat history, MEMORY/SOUL/USER)."),
    ("full", "Full reset", "Also delete ~/.pythinker/api-workspace/ and any docker volumes."),
]


SCOPE_LOOKUP = {
    "config": ResetScope.CONFIG,
    "credentials": ResetScope.CREDENTIALS,
    "sessions": ResetScope.SESSIONS,
    "full": ResetScope.FULL,
}


def sessions_dir() -> Path:
    return get_data_dir() / "sessions"


def api_workspace_dir() -> Path:
    return get_data_dir() / "api-workspace"


def oauth_cli_kit_token_paths() -> list[Path]:
    """Return on-disk OAuth token files Pythinker is responsible for."""
    home = Path.home()
    return [
        home / ".local/share/oauth-cli-kit/auth/oauth.json",
        home / ".local/share/pythinker/auth/github-copilot.json",
    ]


def prompt_scope() -> ResetScope:
    chosen = clack.select(
        "Reset scope?",
        options=SCOPE_OPTIONS,
        default="config",
    )
    return SCOPE_LOOKUP[chosen]


def confirm_typed() -> bool:
    """Final destructive-op consent: user types 'reset' to confirm."""
    typed = clack.text("Type 'reset' to confirm:")
    return typed.strip().lower() == "reset"


def apply_immediate(scope: ResetScope) -> None:
    """Delete credential/session/full paths right after typed-`reset`.

    The config rename is NOT done here — that's deferred to step 14 so the
    user can still recover their config by picking Skip at the summary.
    """
    if scope >= ResetScope.CREDENTIALS:
        for path in oauth_cli_kit_token_paths():
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("reset: could not delete {}: {}", path, exc)
    if scope >= ResetScope.SESSIONS:
        sessions = sessions_dir()
        try:
            shutil.rmtree(sessions)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("reset: could not delete {}: {}", sessions, exc)
    if scope == ResetScope.FULL:
        api_ws = api_workspace_dir()
        try:
            shutil.rmtree(api_ws)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("reset: could not delete {}: {}", api_ws, exc)
        # Best-effort docker volume rm is left to a follow-up.
