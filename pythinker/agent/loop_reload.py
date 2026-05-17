"""Provider hot-reload helpers for the agent loop.

Lifted from ``pythinker/agent/loop.py``. The functions take an ``AgentLoop``
and mutate it in place — the caller stays the loop's own method wrappers,
which preserves test patches against ``AgentLoop._apply_provider_snapshot``
and ``AgentLoop._refresh_provider_snapshot``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from pythinker.providers.limits import clamp_context_window
from pythinker.providers.model_profiles import get_profile

if TYPE_CHECKING:
    from pythinker.agent.loop import AgentLoop
    from pythinker.providers.factory import ProviderSnapshot


def apply_provider_snapshot(loop: "AgentLoop", snapshot: "ProviderSnapshot") -> None:
    """Swap model/provider for future turns without disturbing an active one.

    Cascade the new provider into the runner, subagent manager, consolidator,
    and dream so every component shares one provider object. Same-signature
    snapshots short-circuit early.
    """
    provider = snapshot.provider
    model = snapshot.model
    context_window_tokens = clamp_context_window(
        provider, model, snapshot.context_window_tokens
    )
    if loop.provider is provider and loop.model == model:
        return
    old_model = loop.model
    loop.provider = provider
    loop.model = model
    loop.context_window_tokens = context_window_tokens
    profile = get_profile(model)
    encoding = profile.encoding if profile else "cl100k_base"
    loop._encoding = encoding
    loop.runner.provider = provider
    loop.subagents.set_provider(provider, model)
    loop.consolidator.set_provider(provider, model, context_window_tokens, encoding=encoding)
    loop.dream.set_provider(provider, model)
    loop._provider_signature = snapshot.signature
    logger.info("Runtime model switched for next turn: {} -> {}", old_model, model)


def refresh_provider_snapshot(loop: "AgentLoop") -> None:
    """Pull the latest snapshot and apply it if the signature changed.

    Called at the top of every ``_process_message`` so config edits land at
    the next turn boundary. Errors during load are logged and swallowed —
    a temporarily-broken config must not crash an in-flight session.

    Calls back through ``loop._apply_provider_snapshot`` (the method) so
    test patches against that attribute land.
    """
    if loop._provider_snapshot_loader is None:
        return
    try:
        snapshot = loop._provider_snapshot_loader()
    except Exception:
        logger.exception("Failed to refresh provider config")
        return
    if snapshot.signature == loop._provider_signature:
        return
    loop._apply_provider_snapshot(snapshot)
