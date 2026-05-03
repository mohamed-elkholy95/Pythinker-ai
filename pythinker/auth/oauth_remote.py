"""Automatic-fallback OAuth flow runner.

Always tries the browser callback first. On callback timeout (default 15s),
falls back to a paste prompt that accepts either a raw `code` string or a
full `https://localhost:.../callback?code=...&state=...` URL.

This is the same UX path for both local and SSH-on-remote-host scenarios —
no env-var detection, no `--remote/--local` flag.
"""

from __future__ import annotations

import secrets
from typing import Callable, Protocol, TypeVar, cast
from urllib.parse import parse_qs, urlparse


class StateValidationError(Exception):
    """OAuth `state` parameter did not match — possible phishing or stale URL."""


class CallbackTimeoutError(Exception):
    """Raised by `OAuthClient.wait_for_callback` when no callback arrives in time."""


class OAuthClient(Protocol):
    state: str
    auth_url: str

    def wait_for_callback(self, timeout: float) -> str: ...
    def exchange_code(self, code: str) -> str: ...


_T = TypeVar("_T")


def parse_pasted(text: str) -> tuple[str, str | None]:
    """Accept either a raw code or a full redirect URL; return (code, state_or_none)."""
    text = text.strip()
    if not text:
        raise ValueError("Empty paste")
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        params = parse_qs(parsed.query)
        code = (params.get("code") or [""])[0]
        state = (params.get("state") or [None])[0]
        if not code:
            raise ValueError(f"Could not extract `code` from URL: {text}")
        return code, state
    return text, None


def _prompt_pasted() -> str:
    """Indirection point for testing — overridden in tests via patch."""
    # In production, this is rendered by clack.text(...); for now a plain input.
    return input("Paste the authorization code (or full redirect URL): ")


def run_oauth_with_timeout_fallback(
    login_fn: Callable[..., object],
    *,
    timeout: float = 15.0,
    panel_text: str,
    print_fn: Callable[[str], None] = print,
) -> str:
    """Try browser callback up to `timeout` seconds; on timeout, prompt for paste."""
    client_obj = getattr(login_fn, "__self__", None)
    if client_obj is None:
        raise TypeError("login_fn must be a bound OAuth client login method")
    client = cast(OAuthClient, client_obj)

    if panel_text:
        print_fn(panel_text)
    print_fn(f"Open: {client.auth_url}")
    try:
        return client.wait_for_callback(timeout=timeout)
    except CallbackTimeoutError:
        message = (
            f"OAuth callback did not arrive within {int(timeout * 1000)}ms; "
            + "switching to manual entry (callback_timeout)."
        )
        print_fn(message)
        pasted = _prompt_pasted()
        code, state = parse_pasted(pasted)
        if state is None:
            raise StateValidationError(
                "State missing — paste the full redirect URL "
                "(https://localhost:.../callback?code=...&state=...) "
                "or restart OAuth; bare codes can't be CSRF-validated."
            )
        if not secrets.compare_digest(state, client.state):
            raise StateValidationError("State mismatch — phishing or stale URL; aborting.")
        return client.exchange_code(code)


_SSH_HINT = (
    "SSH/headless tip: if no browser opens, copy the URL printed below "
    "and paste the resulting code or full redirect URL when prompted."
)


def run_oauth_with_hint(
    login_fn: Callable[..., _T],
    *,
    print_fn: Callable[[str], None],
    prompt_fn: Callable[[str], str],
    ssh_hint: str = _SSH_HINT,
) -> _T:
    """Emit an SSH-awareness hint, then delegate to `login_fn`.

    This is the unified entry point used by both ``pythinker provider login``
    and the onboarding wizard.  ``login_fn`` must accept ``print_fn`` and
    ``prompt_fn`` keyword arguments (matching ``oauth_cli_kit``'s
    ``login_oauth_interactive`` signature as well as
    ``pythinker.providers.github_copilot_provider.login_github_copilot``).

    The hint is a single print line — it does not change control flow.
    ``login_oauth_interactive`` already races a browser callback against a
    stdin paste prompt, so SSH users can paste the code without any extra
    wrapping; the hint simply makes that option discoverable upfront.
    """
    print_fn(ssh_hint)
    return login_fn(print_fn=print_fn, prompt_fn=prompt_fn)
