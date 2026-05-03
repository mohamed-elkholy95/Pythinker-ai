"""/status reads web-search credentials via the per-provider slot.

Regression for the bug where cmd_status pulled `search_cfg.api_key` directly,
ignoring the migrated `providers[<active>].api_key` slot. Config-file users
who completed onboarding would see /status report the provider as
unconfigured even after the migration moved their key into `providers`.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from pythinker.bus.events import InboundMessage
from pythinker.command.builtin import cmd_status
from pythinker.command.router import CommandContext
from pythinker.config.schema import (
    WebSearchConfig,
    WebSearchProviderConfig,
    WebToolsConfig,
)


def _make_loop(web_cfg) -> SimpleNamespace:
    consolidator = SimpleNamespace(
        estimate_session_prompt_tokens=lambda _s: (0, 0),
    )
    session = SimpleNamespace(get_history=lambda max_messages=0: [])
    sessions = SimpleNamespace(get_or_create=lambda _k: session)
    subagents = SimpleNamespace(get_running_count_by_session=lambda _k: 0)
    provider = SimpleNamespace(generation=SimpleNamespace(max_tokens=8192))
    return SimpleNamespace(
        consolidator=consolidator,
        sessions=sessions,
        subagents=subagents,
        web_config=web_cfg,
        provider=provider,
        model="test-model",
        _start_time=time.time(),
        _last_usage={},
        _active_tasks={},
        context_window_tokens=128_000,
    )


@pytest.mark.asyncio
async def test_status_reads_per_provider_tavily_slot(monkeypatch):
    """Key under providers["tavily"] must reach fetch_search_usage."""
    web_cfg = WebToolsConfig(
        search=WebSearchConfig(
            provider="tavily",
            providers={"tavily": WebSearchProviderConfig(api_key="tvly-live")},
        ),
    )
    loop = _make_loop(web_cfg)

    captured: dict[str, object] = {}

    async def fake_fetch(*, provider: str, api_key: str | None):
        captured["provider"] = provider
        captured["api_key"] = api_key
        return SimpleNamespace(format=lambda: f"tavily ok ({api_key[:6]})")

    monkeypatch.setattr(
        "pythinker.utils.searchusage.fetch_search_usage", fake_fetch
    )

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="d", content="/status")
    cmd_ctx = CommandContext(
        msg=msg, session=None, key=msg.session_key, raw="/status", args="", loop=loop,
    )

    out = await cmd_status(cmd_ctx)
    assert out is not None
    assert captured["provider"] == "tavily"
    assert captured["api_key"] == "tvly-live"


@pytest.mark.asyncio
async def test_status_falls_back_to_legacy_top_level_api_key(monkeypatch):
    """A cfg without credentials_for() (legacy/duck-typed) must still work.

    Defends against a future refactor that hands /status a stub config
    object: the helper should not crash on configs that pre-date
    WebSearchConfig.credentials_for.
    """
    legacy = SimpleNamespace(
        search=SimpleNamespace(
            provider="tavily",
            api_key="legacy-key",  # no credentials_for, no providers
        ),
    )
    loop = _make_loop(legacy)  # type: ignore[arg-type]

    captured: dict[str, object] = {}

    async def fake_fetch(*, provider: str, api_key: str | None):
        captured["api_key"] = api_key
        return SimpleNamespace(format=lambda: "ok")

    monkeypatch.setattr(
        "pythinker.utils.searchusage.fetch_search_usage", fake_fetch
    )

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="d", content="/status")
    cmd_ctx = CommandContext(
        msg=msg, session=None, key=msg.session_key, raw="/status", args="", loop=loop,
    )

    await cmd_status(cmd_ctx)
    assert captured["api_key"] == "legacy-key"
