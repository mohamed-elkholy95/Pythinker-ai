"""AgentLoop hot-reloads the provider snapshot at the turn boundary.

A config edit to model / provider / api_key must land at the next turn
without restarting the process via `_apply_provider_snapshot` /
`_refresh_provider_snapshot` — and same-signature snapshots must be a
no-op so we don't spend extra CPU per message.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pythinker.bus.queue import MessageBus
from pythinker.providers.factory import ProviderSnapshot


def _make_loop(tmp_path):
    from pythinker.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "old-model"
    provider.generation = MagicMock(max_tokens=4096)

    with patch("pythinker.agent.loop.ContextBuilder"), \
         patch("pythinker.agent.loop.SessionManager"), \
         patch("pythinker.agent.loop.SubagentManager"):
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
    return loop


def _snapshot(provider, model="new-model", ctx_tokens=128_000, sig=("sig",)):
    return ProviderSnapshot(
        provider=provider,
        model=model,
        context_window_tokens=ctx_tokens,
        signature=sig,
    )


def test_apply_provider_snapshot_swaps_provider_and_model(tmp_path):
    loop = _make_loop(tmp_path)
    new_provider = MagicMock()
    new_provider.generation = MagicMock(max_tokens=8192)
    loop._apply_provider_snapshot(_snapshot(new_provider, model="claude-x"))
    assert loop.provider is new_provider
    assert loop.model == "claude-x"
    assert loop.context_window_tokens == 128_000
    assert loop.runner.provider is new_provider


def test_apply_provider_snapshot_no_op_when_signature_matches(tmp_path):
    """Same provider + model must not trigger a cascade swap (perf + churn safety)."""
    loop = _make_loop(tmp_path)
    same_provider = loop.provider
    loop.model = "old-model"
    loop._apply_provider_snapshot(_snapshot(same_provider, model="old-model"))
    # Provider unchanged → runner.provider should not have been re-assigned.
    assert loop.provider is same_provider
    assert loop.model == "old-model"


def test_refresh_pulls_new_snapshot_when_signature_changes(tmp_path):
    loop = _make_loop(tmp_path)
    new_provider = MagicMock()
    new_provider.generation = MagicMock(max_tokens=4096)
    new_snap = _snapshot(new_provider, model="new", sig=("new-sig",))
    loop._provider_snapshot_loader = lambda: new_snap
    loop._provider_signature = ("old-sig",)
    loop._refresh_provider_snapshot()
    assert loop.provider is new_provider
    assert loop.model == "new"
    assert loop._provider_signature == ("new-sig",)


def test_refresh_skips_when_signature_unchanged(tmp_path):
    """If snapshot.signature == loop's, _apply_provider_snapshot must not be called."""
    loop = _make_loop(tmp_path)
    sig = ("stable",)
    loop._provider_signature = sig
    loop._provider_snapshot_loader = lambda: _snapshot(MagicMock(), sig=sig)
    with patch.object(loop, "_apply_provider_snapshot") as apply_spy:
        loop._refresh_provider_snapshot()
    apply_spy.assert_not_called()


def test_refresh_swallows_loader_exceptions(tmp_path):
    """A broken config must not crash an in-flight session."""
    loop = _make_loop(tmp_path)

    def broken_loader():
        raise RuntimeError("config file disappeared")

    loop._provider_snapshot_loader = broken_loader
    # Must not raise; the previous provider stays in place.
    loop._refresh_provider_snapshot()
    assert loop.provider is not None


def test_refresh_is_no_op_when_loader_is_none(tmp_path):
    """Default construction (no loader) keeps a fixed provider for life — backward compat."""
    loop = _make_loop(tmp_path)
    assert loop._provider_snapshot_loader is None
    initial_provider = loop.provider
    loop._refresh_provider_snapshot()
    assert loop.provider is initial_provider
