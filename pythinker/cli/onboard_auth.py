"""OAuth + API-key plumbing helpers for the onboarding wizard.

Split out of ``pythinker/cli/onboard.py``. ``onboard.py`` re-exports
both names below so test patches at
``pythinker.cli.onboard._login_via_oauth_remote`` continue to land where
the wizard reads them — the step that calls ``_login_via_oauth_remote``
goes through the ``pythinker.cli.onboard`` module attribute, not this
file's local symbol.
"""

from __future__ import annotations

from pythinker.config.schema import Config


def _login_via_oauth_remote(provider_name: str) -> None:
    """Bridge to existing OAuth login handlers.

    Looks up `_LOGIN_HANDLERS` from `pythinker.cli.commands` and calls the
    matching handler.  Each registered handler emits an SSH-awareness hint via
    ``pythinker.auth.oauth_remote.run_oauth_with_hint`` before opening the
    browser, so SSH/headless users see the paste-fallback option upfront.

    ``login_oauth_interactive`` (oauth_cli_kit) races a local callback server
    against a stdin paste prompt, so the paste path works without any extra
    timeout wrapper — the hint makes it discoverable.
    """
    from pythinker.cli.commands import _LOGIN_HANDLERS

    handler = _LOGIN_HANDLERS.get(provider_name)
    if handler is None:
        raise RuntimeError(f"No OAuth handler registered for {provider_name}")
    handler()


def _set_provider_api_key(cfg: Config, provider_name: str, value: str) -> None:
    """Set cfg.providers.<provider_name>.api_key.

    Hyphenated provider names map to underscored attribute names.
    Silently no-ops if the schema doesn't know this provider.
    """
    attr = provider_name.replace("-", "_")
    pc = getattr(cfg.providers, attr, None)
    if pc is None:
        return
    pc.api_key = value
