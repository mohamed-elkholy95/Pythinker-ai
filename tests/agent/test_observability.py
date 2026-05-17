"""emit_context_turn_event logs one structured loguru record per turn."""
from __future__ import annotations

from pythinker.agent.observability import emit_context_turn_event


def _capture(caplog, fn) -> None:
    import logging

    from loguru import logger as _loguru

    handler_id = _loguru.add(lambda r: caplog.handler.emit(logging.makeLogRecord({
        "name": r.record["name"],
        "levelno": r.record["level"].no,
        "msg": r.record["message"],
        "args": (),
    })), level="INFO")
    try:
        with caplog.at_level(logging.INFO, logger="pythinker"):
            fn()
    finally:
        _loguru.remove(handler_id)


def test_event_includes_required_fields(caplog):
    def emit():
        emit_context_turn_event(
            session_key="telegram:1",
            model="gpt-5.5",
            window=272_000,
            floor=12_000,
            prompt_est=170_000,
            prompt_actual=174_500,
            zone="amber",
            action="bg_consolidate",
            snip=False,
            microcompact=3,
            encoding="o200k_base",
            metadata_source="curated",
        )

    _capture(caplog, emit)
    assert any("context_turn" in (r.message or "") for r in caplog.records)
    assert any("metadata_source" in (r.message or "") for r in caplog.records)
    assert any("encoding" in (r.message or "") for r in caplog.records)


def test_session_key_is_hashed_in_event(caplog):
    raw_key = "whatsapp:+15551234567@s.whatsapp.net"

    def emit():
        emit_context_turn_event(
            session_key=raw_key,
            model="claude-opus-4-7",
            window=900_000,
            floor=2_000,
            prompt_est=50_000,
            prompt_actual=51_200,
            zone="green",
            action="post_turn",
            snip=False,
            microcompact=0,
            encoding="cl100k_base",
            metadata_source="provider_api",
        )

    _capture(caplog, emit)
    for rec in caplog.records:
        assert "+15551234567" not in (rec.message or "")
        assert "@s.whatsapp.net" not in (rec.message or "")
    assert any("whatsapp:" in (r.message or "") for r in caplog.records)
