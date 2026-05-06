"""Tests for the channel-conditional coding-directives include in identity.md.

Coding-prompt uplift Phase 1: the parallelism + system-reminder paragraphs
must only render on CLI / WebSocket channels (and the empty default).
Chat channels (Telegram, Discord, WhatsApp, Matrix, MS Teams, email) get
the always-on directives but skip the conditional block.
"""

import pytest

from pythinker.utils.prompt_templates import render_template


def _render(channel: str) -> str:
    return render_template(
        "agent/identity.md",
        runtime="test runtime",
        workspace_path="/tmp/wsp",
        platform_policy="",
        channel=channel,
    )


@pytest.mark.parametrize("channel", ["cli", "websocket", ""])
def test_coding_directives_full_block_on_cli_websocket(channel: str):
    out = _render(channel)
    assert "## Coding behavior" in out
    assert "in parallel" in out
    assert "<system-reminder>" in out
    assert "git commit" in out
    assert "default to taking action with tools" in out


@pytest.mark.parametrize(
    "channel",
    ["telegram", "discord", "whatsapp", "matrix", "msteams", "email", "sms"],
)
def test_coding_directives_skip_parallel_block_on_chat_channels(channel: str):
    out = _render(channel)
    assert "## Coding behavior" in out
    assert "git commit" in out
    assert "default to taking action with tools" in out
    assert "in parallel" not in out
    assert "<system-reminder>" not in out


def test_coding_directives_appears_after_search_and_discovery():
    out = _render("cli")
    search_idx = out.find("## Search & Discovery")
    coding_idx = out.find("## Coding behavior")
    assert search_idx >= 0 and coding_idx >= 0
    assert coding_idx > search_idx
