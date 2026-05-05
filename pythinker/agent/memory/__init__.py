"""Memory system: pure file I/O store, lightweight Consolidator, and Dream processor."""

from __future__ import annotations

from pythinker.agent.memory.consolidator import Consolidator
from pythinker.agent.memory.dream import Dream
from pythinker.agent.memory.store import MemoryStore

# `estimate_message_tokens` is re-exported here as the authoritative monkeypatch
# target — Consolidator resolves it through this package at call time so test
# overrides on `pythinker.agent.memory.estimate_message_tokens` are observed.
from pythinker.utils.helpers import estimate_message_tokens

__all__ = [
    "Consolidator",
    "Dream",
    "MemoryStore",
    "estimate_message_tokens",
]
