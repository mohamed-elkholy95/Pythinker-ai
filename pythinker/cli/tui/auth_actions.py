"""Shared TUI helpers for authenticating against an LLM provider.

Extracted out of ``pythinker/cli/tui/commands.py`` so the provider picker can
trigger auth flows on row-select without creating a circular import. Three
entry points talk to these helpers today:

* ``/login`` (in ``pythinker/cli/tui/commands.py``) — opens the provider
  picker, which delegates to ``authenticate_provider`` for ``needs-setup``
  rows.
* ``/provider`` picker (``pythinker/cli/tui/pickers/provider.py``) —
  same call site; the picker is the single auth surface in the TUI.
* Future ``/logout`` picker — uses ``save_api_key_and_reload`` with
  an empty key to clear creds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pythinker.cli.tui.app import TuiApp


async def run_oauth_login_in_terminal(spec) -> tuple[bool, str]:
    """Drive the OAuth/device-code flow for ``spec`` inside the live TUI.

    ``run_in_terminal`` lets prompt_toolkit yield the screen back to plain
    stdio while ``login_oauth_interactive`` (Codex) or ``login_github_copilot``
    (device flow) print URLs and read pasted codes. ``oauth_cli_kit`` already
    detects a running event loop and threads its own ``asyncio.run`` so we
    don't have to hand-roll that.

    Returns ``(ok, detail)`` — ``detail`` is the account_id on success or the
    exception message / sentinel ("cancelled") on failure.
    """
    from prompt_toolkit.application import run_in_terminal

    from pythinker.auth.oauth_remote import run_oauth_with_hint

    def _do() -> tuple[bool, str]:
        if spec.name == "openai_codex":
            try:
                from oauth_cli_kit.flow import login_oauth_interactive
            except Exception as exc:  # noqa: BLE001
                return False, f"oauth_cli_kit unavailable: {exc}"
            login_fn = login_oauth_interactive
        elif spec.name == "github_copilot":
            try:
                from pythinker.providers.github_copilot_provider import (
                    login_github_copilot,
                )
            except Exception as exc:  # noqa: BLE001
                return False, f"copilot login unavailable: {exc}"
            login_fn = login_github_copilot
        else:
            return False, f"OAuth not implemented for {spec.name}"
        print()
        try:
            token = run_oauth_with_hint(
                login_fn,
                print_fn=print,
                prompt_fn=input,
            )
        except (KeyboardInterrupt, EOFError):
            return False, "cancelled"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if not token or not getattr(token, "access", None):
            return False, "no token returned"
        return True, getattr(token, "account_id", None) or "(no account_id)"

    return await run_in_terminal(_do)


async def prompt_api_key(label: str, env_key: str, signup_url: str) -> str:
    """Pause the TUI and read a masked API key from the terminal.

    Uses ``run_in_terminal`` so prompt_toolkit yields the screen to plain
    stdio while ``getpass`` echoes nothing back. Returns the raw key
    (already stripped) or ``""`` if the user cancelled with Ctrl-C / Ctrl-D.
    """
    from getpass import getpass

    from prompt_toolkit.application import run_in_terminal

    def _ask() -> str:
        print()
        print(f"Enter API key for {label} (input hidden):")
        if signup_url:
            print(f"  Get one at: {signup_url}")
        try:
            return getpass(f"  {env_key} > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    raw = await run_in_terminal(_ask)
    return (raw or "").strip()


async def save_api_key_and_reload(app: "TuiApp", spec, key: str) -> str | None:
    """Persist ``key`` under ``providers.<name>.api_key`` and hot-reload.

    Mirrors the model-self-heal path in ``app.py``: copy the live config,
    mutate, build a new provider snapshot, swap it into the agent loop, then
    persist to disk. Returns ``None`` on success or an error string on
    failure (caller surfaces it as a notice).
    """
    from loguru import logger

    from pythinker.config.loader import get_config_path, save_config
    from pythinker.providers.factory import build_provider_snapshot

    try:
        new_config = app.config.model_copy(deep=True)
    except Exception as exc:  # noqa: BLE001
        return f"could not clone config: {exc}"
    provider_cfg = getattr(new_config.providers, spec.name, None)
    if provider_cfg is None:
        return f"no config block for {spec.label}"
    try:
        provider_cfg.api_key = key
    except Exception as exc:  # noqa: BLE001
        return f"could not set api_key: {exc}"
    try:
        snapshot = build_provider_snapshot(new_config)
        app.agent_loop._apply_provider_snapshot(snapshot)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.exception("api-key reload: snapshot apply failed")
        return f"reload failed: {exc}"
    app.config = new_config
    try:
        save_config(new_config, get_config_path())
    except Exception as exc:  # noqa: BLE001
        logger.warning("api-key save: persist failed: {}", exc)
        return f"saved in-memory but could not persist to disk: {exc}"
    return None


async def authenticate_provider(app: "TuiApp", spec) -> tuple[bool, str]:
    """Run the right auth flow for ``spec`` and return ``(ok, detail)``.

    Dispatch:
      * OAuth providers → ``run_oauth_login_in_terminal``
      * api-key / gateway providers → masked prompt → save + hot-reload
      * local / direct providers → no-op success (no creds to manage)
    """
    if getattr(spec, "is_oauth", False):
        return await run_oauth_login_in_terminal(spec)

    if getattr(spec, "is_local", False) or getattr(spec, "is_direct", False):
        return True, "local / direct — no credentials needed"

    key = await prompt_api_key(
        spec.label,
        getattr(spec, "env_key", "API_KEY"),
        getattr(spec, "signup_url", ""),
    )
    if not key:
        return False, "cancelled"
    err = await save_api_key_and_reload(app, spec, key)
    if err:
        return False, err
    return True, "api key saved"
