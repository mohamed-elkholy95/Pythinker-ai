"""Tests for model picker data builder."""
from __future__ import annotations


def test_model_picker_items_dedup_and_mark_current() -> None:
    from pythinker.cli.tui.pickers.model import build_model_items

    class _LoopStub:
        model = "gpt-4o-mini"
        provider_id = "openai"

    class _CfgStub:
        class agents:  # noqa: N801
            class defaults:  # noqa: N801
                model = "gpt-4o-mini"
                alternate_models = ["gpt-4o-mini", "gpt-4.1"]

    items = build_model_items(loop=_LoopStub(), config=_CfgStub())
    ids = [it["model_id"] for it in items]
    assert ids[0] == "gpt-4o-mini"               # current first
    assert "gpt-4.1" in ids
    # no duplicates
    assert len(ids) == len(set(ids))
    # provider fallback fills out at least one extra commonly-known model
    assert len(ids) >= 2
