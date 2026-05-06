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


def _stored_oauth_token(spec):
    """Return a non-empty saved OAuth token for ``spec``, or ``None``.

    Read-only: never triggers an interactive flow. Any failure (missing file,
    parse error, refresh failure with no usable cached token) is treated as
    "no saved token" so the caller falls through to the login picker.
    """
    try:
        if spec.name == "openai_codex":
            from oauth_cli_kit import get_token as _get
            tok = _get()
        elif spec.name == "github_copilot":
            from pythinker.providers.github_copilot_provider import (
                get_github_copilot_login_status as _get,
            )
            tok = _get()
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    if tok and getattr(tok, "access", None):
        return tok
    return None


def _run_oauth_flow(login_fn, *, headless: bool) -> tuple[bool, str]:
    """Run an OAuth login under ``run_in_terminal``. Returns ``(ok, detail)``.

    ``headless=True`` suppresses ``webbrowser.open`` so the URL is just printed
    for the user to open elsewhere (SSH session, sandbox without a browser).
    """
    import webbrowser

    from pythinker.auth.oauth_remote import run_oauth_with_hint

    print()
    original_open = webbrowser.open
    if headless:
        webbrowser.open = lambda *_a, **_kw: False  # type: ignore[assignment]
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
    finally:
        if headless:
            webbrowser.open = original_open  # type: ignore[assignment]
    if not token or not getattr(token, "access", None):
        return False, "no token returned"
    return True, getattr(token, "account_id", None) or "(no account_id)"


async def run_oauth_login_in_terminal(spec, *, headless: bool = False) -> tuple[bool, str]:
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

    return await run_in_terminal(lambda: _run_oauth_flow(login_fn, headless=headless))


async def _show_picker(app: "TuiApp", title: str, options: list[tuple[str, str]]) -> str | None:
    """Render a small choice picker overlay; return the chosen id (or None).

    ``options`` is ``[(id, label), ...]``. Used for the codex auth flow's
    Keep-or-Reauth confirm and the 3-option login-method picker.
    """
    import asyncio

    from pythinker.cli.tui.pickers.fuzzy import FuzzyPickerScreen

    fut: asyncio.Future[str | None] = asyncio.get_event_loop().create_future()

    async def _on_select(item) -> None:
        if not fut.done():
            fut.set_result(item[0])

    class _CancellablePicker(FuzzyPickerScreen):
        def on_cancel(self) -> None:
            if not fut.done():
                fut.set_result(None)

    screen: _CancellablePicker = _CancellablePicker(
        options,
        label_fn=lambda it: it[1],
        on_select=_on_select,
        title=title,
    )
    app.overlay.push(screen)
    app.application.invalidate()
    try:
        return await fut
    finally:
        if app.overlay.top is screen:
            app.overlay.pop()
        app.application.invalidate()


async def prompt_api_key(
    app: "TuiApp", label: str, env_key: str, signup_url: str
) -> str:
    """Open a TUI overlay and read an API key.

    Uses an in-app overlay (``InputDialogScreen``) instead of
    ``run_in_terminal`` + ``getpass`` / ``input()``. Paste arrives through
    prompt_toolkit's regular key pipeline, which decodes bracketed-paste
    sequences into individual character events — so paste works on every
    terminal, the captured value is clean (no escape markers), and the key
    is masked in the display while the length is shown for confirmation.

    Returns the captured key (already trimmed) or ``""`` if the user
    cancelled with Esc.
    """
    import re

    from pythinker.cli.tui.screens.input_dialog import InputDialogScreen

    _printable_ascii = re.compile(r"[^\x21-\x7e]")

    hint_lines = [f"Saves to ~/.pythinker/config.json under providers.{env_key}."]
    if signup_url:
        hint_lines.append(f"Get a key at: {signup_url}")
    screen = InputDialogScreen(
        title=f"API key — {label}",
        prompt=f"{env_key} >",
        hint="\n".join(hint_lines),
        mask=True,
    )
    app.overlay.push(screen)
    app.application.invalidate()
    try:
        raw = await screen.future
    finally:
        # commit() / on_cancel() resolve the future but don't pop the
        # overlay; do that here so the dialog disappears whether the user
        # pressed Enter or Esc.
        if app.overlay.top is screen:
            app.overlay.pop()
        app.application.invalidate()
    if raw is None:
        return ""
    return _printable_ascii.sub("", raw).strip()


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


async def _switch_active_provider(app: "TuiApp", new_id: str) -> str | None:
    """Set ``agents.defaults.provider`` to ``new_id``, rebuild + persist.

    Mirrors the switch block in ``pickers/provider.py``. Returns ``None`` on
    success or an error string on failure. Idempotent — caller can re-invoke
    without ill effect.
    """
    from loguru import logger

    from pythinker.cli.tui.pickers.provider import _default_model_for
    from pythinker.config.loader import get_config_path, save_config
    from pythinker.providers.factory import build_provider_snapshot

    try:
        new_config = app.config.model_copy(deep=True)
        new_config.agents.defaults.provider = new_id
        new_model = await _default_model_for(new_id, new_config)
        if new_model:
            new_config.agents.defaults.model = new_model
        snapshot = build_provider_snapshot(new_config)
        app.agent_loop._apply_provider_snapshot(snapshot)  # noqa: SLF001
        app.config = new_config
        if new_model:
            app.state.model = new_model
        app.state.provider = new_id
    except Exception as exc:  # noqa: BLE001
        logger.exception("provider switch failed during auth flow")
        return f"provider switch failed: {exc}"
    try:
        save_config(new_config, get_config_path())
    except Exception as exc:  # noqa: BLE001
        logger.warning("provider switch: persist failed: {}", exc)
        return f"switched in-session, but persisting failed: {exc}"
    return None


async def _codex_login_flow(app: "TuiApp", spec) -> tuple[bool, str, str | None]:
    """Run the OpenAI Codex login picker flow.

    Returns ``(ok, detail, switched_to)``. When ``switched_to`` is non-None,
    the active provider has already been fully switched (config mutated,
    snapshot applied, persisted) and the caller must NOT redo the switch.
    """
    choice = await _show_picker(
        app,
        "OpenAI Codex login",
        [
            ("browser", "Browser login — ChatGPT Pro/Plus subscription"),
            ("headless", "Headless login — no browser, paste callback URL (SSH-friendly)"),
            ("api_key", "Paste API key — use OpenAI API directly (switches to OpenAI provider)"),
        ],
    )
    if choice is None:
        return False, "cancelled", None
    if choice == "browser":
        ok, detail = await run_oauth_login_in_terminal(spec, headless=False)
        return ok, detail, None
    if choice == "headless":
        ok, detail = await run_oauth_login_in_terminal(spec, headless=True)
        return ok, detail, None
    if choice == "api_key":
        from pythinker.providers.registry import PROVIDERS
        openai_spec = next((s for s in PROVIDERS if s.name == "openai"), None)
        if openai_spec is None:
            return False, "openai provider missing from registry", None
        key = await prompt_api_key(
            app,
            openai_spec.label,
            getattr(openai_spec, "env_key", "OPENAI_API_KEY"),
            getattr(openai_spec, "signup_url", ""),
        )
        if not key:
            return False, "cancelled", None
        err = await save_api_key_and_reload(app, openai_spec, key)
        if err:
            return False, err, None
        switch_err = await _switch_active_provider(app, "openai")
        if switch_err:
            return False, switch_err, None
        return True, "api key saved; switched to OpenAI", "openai"
    return False, f"unknown auth method: {choice}", None


async def authenticate_provider(
    app: "TuiApp", spec
) -> tuple[bool, str, str | None]:
    """Run the right auth flow for ``spec``. Returns ``(ok, detail, switched_to)``.

    ``switched_to`` is ``None`` for normal flows; when non-None, the auth
    helper has already fully switched the active provider (e.g. the Codex
    "paste API key" branch redirects to the ``openai`` provider) and the
    caller must NOT redo the provider switch.

    Dispatch:
      * OAuth + saved token → Keep-or-Reauth confirm picker
      * ``openai_codex`` reauth path → 3-option picker
        (browser OAuth / headless OAuth / paste OpenAI API key)
      * other OAuth providers → ``run_oauth_login_in_terminal``
      * api-key / gateway providers → masked prompt → save + hot-reload
      * local / direct providers → no-op success (no credentials to manage)
    """
    if getattr(spec, "is_oauth", False):
        existing = _stored_oauth_token(spec)
        if existing is not None:
            account = getattr(existing, "account_id", None) or "(no account_id)"
            choice = await _show_picker(
                app,
                f"{spec.label} — saved token found",
                [
                    ("keep", f"Keep saved token ({account})"),
                    ("reauth", "Re-login (replace saved token)"),
                ],
            )
            if choice is None:
                return True, f"kept saved token ({account})", None
            if choice == "keep":
                return True, f"using saved token ({account})", None
            # fall through to re-auth

        if spec.name == "openai_codex":
            return await _codex_login_flow(app, spec)

        ok, detail = await run_oauth_login_in_terminal(spec)
        return ok, detail, None

    if getattr(spec, "is_local", False) or getattr(spec, "is_direct", False):
        return True, "local / direct — no credentials needed", None

    key = await prompt_api_key(
        app,
        spec.label,
        getattr(spec, "env_key", "API_KEY"),
        getattr(spec, "signup_url", ""),
    )
    if not key:
        return False, "cancelled", None
    err = await save_api_key_and_reload(app, spec, key)
    if err:
        return False, err, None
    return True, "api key saved", None
