"""Tests for ``pythinker.cli.tui.app._self_heal_local_model``.

The helper validates the configured model against a live local provider
and auto-recovers when the configured id has gone stale (e.g. the user
swapped which model is loaded in LM Studio without editing config.json).

We never hit a real local server — ``list_local_models`` is monkey-patched
per test to return shaped fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pythinker.cli.tui import app as app_mod
from pythinker.providers.local_models import LocalModel


@dataclass
class _FakeStatusBar:
    refreshes: int = 0

    def refresh(self) -> None:
        self.refreshes += 1


@dataclass
class _FakeChatPane:
    notices: list[tuple[str, str]] = None  # type: ignore[assignment]
    welcome_updates: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.notices = []
        self.welcome_updates = []

    def append_notice(self, text: str, *, kind: str = "info") -> None:
        self.notices.append((kind, text))

    def set_welcome_context(self, **kwargs: Any) -> None:
        self.welcome_updates.append(kwargs)


class _FakeApplication:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1


@dataclass
class _FakeState:
    model: str
    provider: str = "lm_studio"


@dataclass
class _FakeLoop:
    """Minimal stand-in for AgentLoop._apply_provider_snapshot."""

    last_snapshot: Any = None

    def _apply_provider_snapshot(self, snapshot: Any) -> None:
        self.last_snapshot = snapshot


class _FakeConfig:
    """Minimal config wrapper exposing the bits the self-heal helper reads."""

    def __init__(self, model: str, provider: str = "lm_studio", api_base: str | None = None) -> None:
        from types import SimpleNamespace

        self.agents = SimpleNamespace(defaults=SimpleNamespace(model=model))
        self._provider = provider
        provider_cfg = SimpleNamespace(api_base=api_base) if api_base else None
        self.providers = SimpleNamespace(**{provider: provider_cfg}) if provider_cfg is not None else SimpleNamespace()

    def get_provider_name(self, _model: str) -> str:
        return self._provider

    def model_copy(self, *, deep: bool = False) -> "_FakeConfig":
        # Simplified copy: just clone the model id.
        return _FakeConfig(
            model=self.agents.defaults.model,
            provider=self._provider,
            api_base=getattr(getattr(self.providers, self._provider, None), "api_base", None),
        )


def _make_app(model: str, *, api_base: str | None = "http://localhost:1234/v1") -> Any:
    """Build a minimal TuiApp-shaped object the helper can operate on."""
    from types import SimpleNamespace

    config = _FakeConfig(model=model, provider="lm_studio", api_base=api_base)
    return SimpleNamespace(
        config=config,
        agent_loop=_FakeLoop(),
        state=_FakeState(model=model),
        chat_pane=_FakeChatPane(),
        status_bar=_FakeStatusBar(),
        application=_FakeApplication(),
    )


# ---------------------------------------------------------------------------
# Happy path: configured id matches a loaded model → no-op
# ---------------------------------------------------------------------------


async def test_no_op_when_configured_model_is_loaded(monkeypatch) -> None:
    app = _make_app(model="gemma3")

    async def _fake_list_local_models(*, provider_id, api_base, **kw):
        return [LocalModel(model_id="gemma3", loaded=True)]

    monkeypatch.setattr(app_mod, "list_local_models", _fake_list_local_models)

    await app_mod._self_heal_local_model(app)

    assert app.chat_pane.notices == []
    assert app.state.model == "gemma3"
    assert app.agent_loop.last_snapshot is None


# ---------------------------------------------------------------------------
# Auto-switch path: stale id + exactly one loaded model
# ---------------------------------------------------------------------------


async def test_auto_switches_when_configured_missing_and_one_loaded(monkeypatch) -> None:
    app = _make_app(model="qwen/qwen3.6-27b")

    async def _fake_list_local_models(*, provider_id, api_base, **kw):
        return [
            LocalModel(model_id="google/gemma-4-e4b", loaded=True),
            LocalModel(model_id="qwen/qwen3.6-27b", loaded=False),  # downloaded but not loaded
        ]

    monkeypatch.setattr(app_mod, "list_local_models", _fake_list_local_models)

    # Stub out the snapshot + save side effects so we don't depend on
    # the full provider factory in this unit test.
    sentinels: dict[str, Any] = {}

    def _fake_build_snapshot(cfg):
        sentinels["snapshot_for"] = cfg.agents.defaults.model
        return "SNAPSHOT"

    def _fake_save(cfg, path):
        sentinels["saved_model"] = cfg.agents.defaults.model

    def _fake_get_config_path():
        return "/tmp/fake-config.json"

    monkeypatch.setattr(
        "pythinker.providers.factory.build_provider_snapshot",
        _fake_build_snapshot,
    )
    monkeypatch.setattr("pythinker.config.loader.save_config", _fake_save)
    monkeypatch.setattr("pythinker.config.loader.get_config_path", _fake_get_config_path)

    await app_mod._self_heal_local_model(app)

    assert app.state.model == "google/gemma-4-e4b"
    assert app.agent_loop.last_snapshot == "SNAPSHOT"
    assert sentinels["snapshot_for"] == "google/gemma-4-e4b"
    assert sentinels["saved_model"] == "google/gemma-4-e4b"
    # Status bar refreshed and a notice was appended.
    assert app.status_bar.refreshes == 1
    assert any("auto-switched" in note for _, note in app.chat_pane.notices)
    # Welcome card gets the new id so an empty session rerender doesn't
    # display the stale value.
    assert app.chat_pane.welcome_updates == [{"model": "google/gemma-4-e4b"}]


# ---------------------------------------------------------------------------
# Ambiguous path: stale id + multiple loaded → warn, don't switch
# ---------------------------------------------------------------------------


async def test_warns_when_multiple_loaded_models(monkeypatch) -> None:
    app = _make_app(model="qwen/qwen3.6-27b")

    async def _fake_list_local_models(*, provider_id, api_base, **kw):
        return [
            LocalModel(model_id="a", loaded=True),
            LocalModel(model_id="b", loaded=True),
            LocalModel(model_id="qwen/qwen3.6-27b", loaded=False),
        ]

    monkeypatch.setattr(app_mod, "list_local_models", _fake_list_local_models)

    await app_mod._self_heal_local_model(app)

    # Configured id is unchanged; a single warn-level notice describes the
    # situation and points the user at /model.
    assert app.state.model == "qwen/qwen3.6-27b"
    assert app.agent_loop.last_snapshot is None
    assert len(app.chat_pane.notices) == 1
    kind, text = app.chat_pane.notices[0]
    assert kind == "warn"
    assert "/model" in text


# ---------------------------------------------------------------------------
# Server unreachable: warn once, leave config alone
# ---------------------------------------------------------------------------


async def test_warn_when_server_unreachable(monkeypatch) -> None:
    app = _make_app(model="anything")

    async def _fake_list_local_models(*, provider_id, api_base, **kw):
        return []  # connection refused / timeout / 404 → empty list

    monkeypatch.setattr(app_mod, "list_local_models", _fake_list_local_models)

    await app_mod._self_heal_local_model(app)

    assert app.state.model == "anything"  # untouched
    assert len(app.chat_pane.notices) == 1
    assert app.chat_pane.notices[0][0] == "warn"
    assert "not reachable" in app.chat_pane.notices[0][1]


# ---------------------------------------------------------------------------
# Skip path: non-local provider → no probe at all
# ---------------------------------------------------------------------------


async def test_skips_non_local_provider(monkeypatch) -> None:
    """Anthropic / OpenAI-Codex / etc. don't need this probe — the helper
    must early-return before calling list_local_models."""
    app = _make_app(model="claude-sonnet-4-5")
    app.config._provider = "anthropic"  # non-local

    called = {"n": 0}

    async def _fake_list_local_models(**kw):
        called["n"] += 1
        return []

    monkeypatch.setattr(app_mod, "list_local_models", _fake_list_local_models)

    await app_mod._self_heal_local_model(app)

    assert called["n"] == 0
    assert app.chat_pane.notices == []
