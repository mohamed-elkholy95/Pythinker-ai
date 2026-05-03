"""Tests for telemetry sinks and the module-level emit() indirection."""

import json
from pathlib import Path

from pythinker.runtime.context import RequestContext
from pythinker.runtime.telemetry import (
    CompositeSink,
    JSONLSink,
    LoggingSink,
    TelemetryEvent,
    emit,
    set_sink,
)


def _ctx() -> RequestContext:
    return RequestContext.for_inbound(
        channel="cli", sender_id="u", chat_id="c", session_key="cli:c",
    )


def test_jsonl_sink_writes_one_line_per_event(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    sink = JSONLSink(log)
    ctx = _ctx()
    sink.emit(TelemetryEvent(name="turn_started", context=ctx, attributes={"lock_wait_s": 0.01}))
    sink.emit(TelemetryEvent(name="tool_call", context=ctx, attributes={"tool": "read_file", "allowed": True}))
    sink.close()

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["name"] == "turn_started"
    assert rec0["trace_id"] == ctx.trace_id
    # Pseudonymized: raw session_key must NEVER appear in the record.
    assert "session_key" not in rec0
    assert rec0["session_key_hash"] != "cli:c"
    assert len(rec0["session_key_hash"]) == 12
    assert rec0["attributes"] == {"lock_wait_s": 0.01}
    rec1 = json.loads(lines[1])
    assert rec1["name"] == "tool_call"
    assert rec1["attributes"]["tool"] == "read_file"


def test_jsonl_sink_drops_unknown_attribute_keys(tmp_path: Path):
    """Allow-listed schema: unexpected attribute keys are filtered before write."""
    log = tmp_path / "events.jsonl"
    sink = JSONLSink(log)
    ctx = _ctx()
    sink.emit(TelemetryEvent(
        name="tool_call", context=ctx,
        # "tool" is allowed; "user_email" is NOT — must be dropped.
        attributes={"tool": "read_file", "user_email": "alice@example.com"},
    ))
    sink.close()
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert "user_email" not in rec["attributes"]
    assert "user_email" not in json.dumps(rec)
    assert rec["attributes"] == {"tool": "read_file"}


def test_emit_drops_unknown_event_name(tmp_path: Path, monkeypatch):
    """Unknown event names are rejected at emit() so audit logs stay schema-clean."""
    log = tmp_path / "events.jsonl"
    set_sink(JSONLSink(log))
    try:
        emit("turn_started", _ctx(), {"lock_wait_s": 0.01})  # known
        emit("totally_made_up_event", _ctx(), {})            # unknown — dropped
    finally:
        set_sink(None)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["name"] == "turn_started"


def test_logging_sink_uses_loguru(monkeypatch):
    """Patch logger.info directly — loguru does not bridge into pytest's caplog by default."""
    captured: list[tuple[str, tuple, dict]] = []

    def _capture(template, *args, **kwargs):
        captured.append((template, args, kwargs))

    import pythinker.runtime.telemetry as telemetry_mod
    monkeypatch.setattr(telemetry_mod.logger, "info", _capture)

    sink = LoggingSink()
    ctx = _ctx()
    sink.emit(TelemetryEvent(name="turn_started", context=ctx, attributes={"k": "v"}))

    assert len(captured) == 1
    template, args, _ = captured[0]
    rendered = template.format(*args)
    assert "turn_started" in rendered
    assert ctx.trace_id in rendered


def test_composite_sink_fans_out_to_all_children():
    seen_a: list[str] = []
    seen_b: list[str] = []

    class _Capture:
        def __init__(self, bucket):
            self.bucket = bucket

        def emit(self, event):
            self.bucket.append(event.name)

        def close(self):
            pass

    sink = CompositeSink([_Capture(seen_a), _Capture(seen_b)])
    sink.emit(TelemetryEvent(name="turn_finished", context=_ctx(), attributes={}))
    assert seen_a == ["turn_finished"]
    assert seen_b == ["turn_finished"]


def test_module_emit_uses_active_sink(tmp_path: Path):
    log = tmp_path / "active.jsonl"
    sink = JSONLSink(log)
    set_sink(sink)
    try:
        emit("policy_decision", _ctx(), {"allowed": True, "tool": "read_file"})
    finally:
        sink.close()
        set_sink(None)

    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert rec["name"] == "policy_decision"
    assert rec["attributes"] == {"allowed": True, "tool": "read_file"}


def test_emit_with_no_sink_is_noop():
    set_sink(None)
    # Must not raise even when no sink is installed.
    emit("turn_started", _ctx(), {})


def test_install_telemetry_sink_closes_previous_sink(tmp_path: Path):
    """Bootstrap must close the previously-installed sink, not just discard it.

    Without close-on-replace, a long-lived process that re-builds Config (e.g.
    config reload, repeated `Pythinker.from_config(...)`) would leak any open
    file handle the previous JSONLSink owned.
    """
    from pythinker.config.schema import Config, RuntimeConfig
    from pythinker.runtime._bootstrap import install_telemetry_sink

    log_a = tmp_path / "a.jsonl"
    log_b = tmp_path / "b.jsonl"

    cfg_a = Config(runtime=RuntimeConfig(telemetry_sink="jsonl", telemetry_jsonl_path=str(log_a)))
    cfg_b = Config(runtime=RuntimeConfig(telemetry_sink="jsonl", telemetry_jsonl_path=str(log_b)))

    install_telemetry_sink(cfg_a)
    from pythinker.runtime.telemetry import get_sink
    first = get_sink()
    assert first is not None
    assert hasattr(first, "_fp") and not first._fp.closed

    try:
        install_telemetry_sink(cfg_b)
        # The previous sink's file pointer must now be closed; the new one open.
        assert first._fp.closed is True
        second = get_sink()
        assert second is not None and second is not first
        assert not second._fp.closed
    finally:
        set_sink(None)
        try:
            first._fp.close()
            second._fp.close()
        except Exception:
            pass
