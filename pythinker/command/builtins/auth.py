"""Auth-related slash commands: ``/login`` and ``/logout``.

Mirror the ``pythinker provider login`` and ``pythinker auth logout`` CLI
subcommands so users can manage OAuth credentials from any chat surface
(WebUI, Telegram, interactive CLI). The login flow itself is browser- /
device-code-driven and cannot be completed inside a chat turn, so
``/login`` only reports state and points at the terminal command. ``/logout``
performs the token-file deletion in-process.

Provider coverage is registry-driven: every provider in
:mod:`pythinker.providers.registry` with ``is_oauth=True`` is included
automatically. Token-file location is resolved from the spec's
``token_filename`` / ``token_app_name`` fields, falling back to
``oauth_cli_kit``'s default storage. Non-OAuth providers (api-key, gateway,
local, direct) are reported with an actionable message — there's no token
file to delete; the user clears credentials via config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pythinker.bus.events import OutboundMessage
from pythinker.command.router import CommandContext


def _oauth_specs() -> list[Any]:
    from pythinker.providers.registry import PROVIDERS

    return [s for s in PROVIDERS if getattr(s, "is_oauth", False)]


def _resolve_spec(arg: str) -> Any | None:
    """Look up a provider spec by user-supplied name (dashes or underscores)."""
    from pythinker.providers.registry import find_by_name

    return find_by_name(arg.strip().replace("-", "_"))


def _file_storage(spec) -> Any | None:
    """Build a ``FileTokenStorage`` matching ``spec``'s registry fields.

    Returns ``None`` if oauth_cli_kit isn't importable.
    """
    try:
        from oauth_cli_kit import FileTokenStorage
    except Exception:
        return None

    kwargs: dict[str, Any] = {}
    if getattr(spec, "token_filename", ""):
        kwargs["token_filename"] = spec.token_filename
    if getattr(spec, "token_app_name", ""):
        kwargs["app_name"] = spec.token_app_name
    return FileTokenStorage(**kwargs)


def _token_path(spec) -> Path | None:
    """Return the on-disk OAuth token file for ``spec``, or ``None``."""
    storage = _file_storage(spec)
    if storage is None:
        return None
    try:
        return storage.get_token_path()
    except Exception:
        return None


def _auth_state(spec) -> tuple[str, str]:
    """Return ``(state, detail)`` for one OAuth provider — read-only.

    Reads the token file directly via ``FileTokenStorage.load()`` so we
    never trigger a refresh or interactive flow from a chat turn.
    """
    storage = _file_storage(spec)
    if storage is None:
        return "ERROR", "oauth_cli_kit not installed"
    try:
        tok = storage.load()
    except Exception as exc:
        return "MISSING", f"unreadable token ({type(exc).__name__})"
    if not tok or not getattr(tok, "access", None):
        return "MISSING", "no stored token"
    return "AUTHENTICATED", getattr(tok, "account_id", None) or "(no account_id)"


def _supported_names() -> str:
    return ", ".join(s.name.replace("_", "-") for s in _oauth_specs()) or "(none)"


def _outbound(msg, content: str) -> OutboundMessage:
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=content,
        metadata={**dict(msg.metadata or {}), "render_as": "text"},
    )


async def cmd_login(ctx: CommandContext) -> OutboundMessage:
    """Show OAuth auth state and the exact command needed to (re-)authenticate.

    Login is a browser / device-code flow that has to run on the user's local
    machine, so we don't try to drive it from a chat turn — we surface
    state + the terminal command.
    """
    msg = ctx.msg
    arg = (ctx.args or "").strip()
    specs = _oauth_specs()

    if arg:
        spec = _resolve_spec(arg)
        if spec is None:
            body = (
                f"Unknown provider: {arg!r}\n"
                f"OAuth providers: {_supported_names()}"
            )
            return _outbound(msg, body)
        if not getattr(spec, "is_oauth", False):
            body = (
                f"{spec.label} is not an OAuth provider — it uses an API key.\n"
                f"Set it in your config under providers.{spec.name}.api_key "
                f"(or re-run `pythinker onboard`)."
            )
            return _outbound(msg, body)
        state, detail = _auth_state(spec)
        body = (
            f"{spec.label}: {state} — {detail}\n"
            f"\nRun on the host where pythinker is installed:\n"
            f"  pythinker provider login {spec.name.replace('_', '-')}"
        )
        return _outbound(msg, body)

    if not specs:
        return _outbound(msg, "No OAuth providers are registered.")

    lines = ["OAuth providers:"]
    for spec in specs:
        state, detail = _auth_state(spec)
        lines.append(f"  • {spec.label}: {state} — {detail}")
    lines.append("")
    lines.append("To (re-)authenticate, run on the pythinker host:")
    lines.append(f"  pythinker provider login <provider>   ({_supported_names()})")
    return _outbound(msg, "\n".join(lines))


async def cmd_logout(ctx: CommandContext) -> OutboundMessage:
    """Delete the stored OAuth token for a provider.

    Without an argument: list OAuth providers. With a provider name: unlink
    its token file in-process. Non-OAuth providers report an actionable
    config-edit hint instead of an error.
    """
    msg = ctx.msg
    arg = (ctx.args or "").strip()

    if not arg:
        body = (
            "Usage: /logout <provider>\n"
            f"OAuth providers: {_supported_names()}"
        )
        return _outbound(msg, body)

    spec = _resolve_spec(arg)
    if spec is None:
        body = (
            f"Unknown provider: {arg!r}\n"
            f"OAuth providers: {_supported_names()}"
        )
        return _outbound(msg, body)

    if not getattr(spec, "is_oauth", False):
        body = (
            f"{spec.label} is not an OAuth provider — there's no token to delete.\n"
            f"To clear the API key, edit providers.{spec.name}.api_key in your config."
        )
        return _outbound(msg, body)

    path = _token_path(spec)
    if path is None:
        return _outbound(msg, f"Don't know where {spec.label} stores its token.")
    if not path.exists():
        return _outbound(msg, f"No stored {spec.label} token — nothing to do.")
    try:
        path.unlink()
    except OSError as exc:
        return _outbound(msg, f"Could not delete {path}: {exc}")
    body = (
        f"✓ {spec.label} logged out.\n"
        f"Re-auth with: pythinker provider login "
        f"{spec.name.replace('_', '-')}"
    )
    return _outbound(msg, body)
