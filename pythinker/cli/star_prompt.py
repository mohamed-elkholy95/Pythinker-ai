"""One-time GitHub star prompt shown at first interactive launch.

TTY-only, gh-CLI-only, state-file-gated, mark-before-ask. Adds a
PYTHINKER_NO_STAR_PROMPT env-var escape hatch and probes `gh auth
status` (not `gh --version`) so an installed-but-logged-out gh skips
silently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pythinker.config.paths import get_runtime_subdir

REPO = "mohamed-elkholy95/Pythinker-ai"
PROMPT_TEXT = "[pythinker] Enjoying Pythinker? Star it on GitHub? [Y/n] "
SUCCESS_TEXT = "[pythinker] Thanks for the star!"
FAILURE_PREFIX = "[pythinker] Could not star repository automatically: "
ENV_OPT_OUT = "PYTHINKER_NO_STAR_PROMPT"
GH_AUTH_TIMEOUT_S = 3
GH_STAR_TIMEOUT_S = 10
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

StarRepoResult = tuple[bool, str]  # (ok, error_message_or_empty)


def _state_path() -> Path:
    """Path to the JSON file that records whether the prompt has fired."""
    return get_runtime_subdir("state") / "star-prompt.json"


def _has_been_prompted() -> bool:
    """Return True iff the state file exists and contains a string `prompted_at`."""
    path = _state_path()
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("prompted_at"), str)


def _mark_prompted() -> None:
    """Write the state file with the current UTC timestamp.

    Called BEFORE the prompt is displayed so that Ctrl+C, crash, or hang
    does not cause re-prompting next launch. This is the single most
    important invariant in the design.
    """
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"prompted_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _is_gh_authenticated() -> bool:
    """Return True iff `gh auth status` exits 0.

    Single probe covers both "gh is missing" (FileNotFoundError) and
    "gh present but logged out" (non-zero exit). Times out at
    GH_AUTH_TIMEOUT_S; treats timeout as False. CREATE_NO_WINDOW
    suppresses the Windows console-window flash; constant is 0 on
    Linux/macOS so the call is identical there.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            timeout=GH_AUTH_TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _star_repo() -> StarRepoResult:
    """PUT /user/starred/<REPO> via gh; return (ok, error_message)."""
    try:
        result = subprocess.run(
            ["gh", "api", "-X", "PUT", f"/user/starred/{REPO}"],
            capture_output=True,
            text=True,
            timeout=GH_STAR_TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    err = (result.stderr or "").strip() or (result.stdout or "").strip() or f"gh exited {result.returncode}"
    return False, err


def _ask_yes_no(prompt: str) -> bool:
    """Read one line; treat empty / y / yes (any case, trimmed) as yes."""
    answer = input(prompt).strip().lower()
    return answer in ("", "y", "yes")


_TRUTHY_ENV = {"1", "true", "yes"}


def _env_opt_out() -> bool:
    raw = os.environ.get(ENV_OPT_OUT, "").strip().lower()
    return raw in _TRUTHY_ENV


def maybe_prompt_github_star(
    *,
    stdin_is_tty: bool | None = None,
    stdout_is_tty: bool | None = None,
    has_been_prompted_fn: Callable[[], bool] | None = None,
    is_gh_authenticated_fn: Callable[[], bool] | None = None,
    mark_prompted_fn: Callable[[], None] | None = None,
    ask_yes_no_fn: Callable[[str], bool] | None = None,
    star_repo_fn: Callable[[], StarRepoResult] | None = None,
    log_fn: Callable[[str], None] | None = None,
    warn_fn: Callable[[str], None] | None = None,
) -> None:
    """Show the GitHub star prompt at most once per user.

    Every helper is overridable for tests; production callers pass nothing.
    """
    stdin_tty = sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
    stdout_tty = sys.stdout.isatty() if stdout_is_tty is None else stdout_is_tty
    if not stdin_tty or not stdout_tty:
        return
    if _env_opt_out():
        return

    has_prompted = has_been_prompted_fn or _has_been_prompted
    if has_prompted():
        return

    is_authed = is_gh_authenticated_fn or _is_gh_authenticated
    if not is_authed():
        return  # NB: state file NOT marked; user gets prompted once they install/auth gh

    # Mark BEFORE asking so Ctrl+C / crash never causes re-prompt.
    mark = mark_prompted_fn or _mark_prompted
    mark()

    ask = ask_yes_no_fn or _ask_yes_no
    if not ask(PROMPT_TEXT):
        return

    star = star_repo_fn or _star_repo
    ok, err = star()
    if ok:
        log = log_fn or print
        log(SUCCESS_TEXT)
        return
    warn = warn_fn or print
    warn(f"{FAILURE_PREFIX}{err}")
