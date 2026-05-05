"""Tests for ``/login`` and ``/logout`` slash commands."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pythinker.bus.events import InboundMessage
from pythinker.command.builtin import cmd_login, cmd_logout
from pythinker.command.router import CommandContext


def _ctx(raw: str, *, args: str = "") -> CommandContext:
    msg = InboundMessage(
        channel="websocket",
        sender_id="u1",
        chat_id="c1",
        content=raw,
        metadata={"source": "test"},
    )
    return CommandContext(
        msg=msg, session=None, key=msg.session_key,
        raw=raw, args=args, loop=SimpleNamespace(),
    )


# ---------- /login ----------


async def test_login_lists_all_oauth_providers() -> None:
    out = await cmd_login(_ctx("/login"))
    assert "OAuth providers" in out.content
    # Both registered OAuth providers must appear in the listing.
    assert "OpenAI Codex" in out.content
    assert "Github Copilot" in out.content
    assert "pythinker provider login" in out.content
    assert out.metadata["render_as"] == "text"


async def test_login_unknown_provider_lists_supported() -> None:
    out = await cmd_login(_ctx("/login bogus", args="bogus"))
    assert "Unknown provider" in out.content
    assert "openai-codex" in out.content
    assert "github-copilot" in out.content


async def test_login_non_oauth_provider_explains() -> None:
    # Anthropic is api-key, not OAuth.
    out = await cmd_login(_ctx("/login anthropic", args="anthropic"))
    assert "not an OAuth provider" in out.content
    assert "providers.anthropic.api_key" in out.content


async def test_login_specific_provider_shows_state_and_command() -> None:
    out = await cmd_login(_ctx("/login openai-codex", args="openai-codex"))
    assert "OpenAI Codex" in out.content
    assert "pythinker provider login openai-codex" in out.content


async def test_login_accepts_underscore_form() -> None:
    out = await cmd_login(_ctx("/login github_copilot", args="github_copilot"))
    assert "Github Copilot" in out.content
    # Output normalises back to dash form for the suggested CLI command.
    assert "pythinker provider login github-copilot" in out.content


# ---------- /logout ----------


async def test_logout_without_args_shows_usage() -> None:
    out = await cmd_logout(_ctx("/logout"))
    assert "Usage: /logout" in out.content
    assert "openai-codex" in out.content


async def test_logout_unknown_provider() -> None:
    out = await cmd_logout(_ctx("/logout bogus", args="bogus"))
    assert "Unknown provider" in out.content


async def test_logout_non_oauth_provider() -> None:
    out = await cmd_logout(_ctx("/logout openai", args="openai"))
    assert "not an OAuth provider" in out.content
    assert "providers.openai.api_key" in out.content


async def test_logout_no_stored_token(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.json"
    with patch(
        "pythinker.command.builtins.auth._token_path", return_value=missing
    ):
        out = await cmd_logout(_ctx("/logout openai-codex", args="openai-codex"))
    assert "No stored OpenAI Codex token" in out.content


async def test_logout_deletes_token_file(tmp_path) -> None:
    token_file = tmp_path / "auth" / "oauth.json"
    token_file.parent.mkdir()
    token_file.write_text('{"access":"x","refresh":"y","expires":1}')
    with patch(
        "pythinker.command.builtins.auth._token_path", return_value=token_file
    ):
        out = await cmd_logout(_ctx("/logout openai-codex", args="openai-codex"))
    assert not token_file.exists()
    assert "logged out" in out.content
    assert "pythinker provider login openai-codex" in out.content


async def test_logout_unlink_failure_surfaces_error(tmp_path) -> None:
    token_file = tmp_path / "auth.json"
    token_file.write_text("{}")
    with patch(
        "pythinker.command.builtins.auth._token_path", return_value=token_file
    ), patch("pathlib.Path.unlink", side_effect=OSError("boom")):
        out = await cmd_logout(_ctx("/logout github-copilot", args="github-copilot"))
    assert "Could not delete" in out.content
    assert "boom" in out.content


# ---------- registry / metadata coverage ----------


def test_login_logout_registered_in_router() -> None:
    from pythinker.command.builtin import register_builtin_commands
    from pythinker.command.router import CommandRouter

    router = CommandRouter()
    register_builtin_commands(router)

    assert router.is_dispatchable_command("/login")
    assert router.is_dispatchable_command("/login openai-codex")
    assert router.is_dispatchable_command("/logout")
    assert router.is_dispatchable_command("/logout openai-codex")


def test_login_logout_have_metadata() -> None:
    from pythinker.command.metadata import BUILTIN_COMMAND_METADATA

    names = {m.name for m in BUILTIN_COMMAND_METADATA}
    assert "/login" in names
    assert "/logout" in names


@pytest.fixture(autouse=True)
def _no_oauth_kit(monkeypatch):
    """The CI environment may not have a stored token; nothing to do.

    The real ``oauth_cli_kit.FileTokenStorage`` is fine in tests because
    ``.load()`` returns ``None`` for missing files. This fixture exists as a
    placeholder so future failure modes can be patched in one place.
    """
    yield
