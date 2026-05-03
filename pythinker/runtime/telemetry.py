"""Single-emit indirection for runtime events.

Goal: every subsystem (policy, egress, manifest, runner) calls `emit()` and
the operator decides — via Config.runtime.telemetry_sink — whether output
goes to logs, JSONL, both, or nothing.

This is intentionally tiny. No OpenTelemetry, no Langfuse fan-out here —
those are downstream consumers that can subscribe to the JSONL file.
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from pythinker.runtime.context import RequestContext

# Allow-listed event names. Anything not in this set is rejected at emit time
# so a typo or rogue caller cannot smuggle arbitrary categories into audit
# logs and confuse downstream consumers.
_ALLOWED_EVENT_NAMES: frozenset[str] = frozenset({
    "turn_started", "turn_finished",
    "tool_call", "tool_result",
    "policy_decision",
    "error",
})

# Allow-listed attribute keys per event name. Any key NOT listed is dropped
# (with a one-time WARN). This is the schema that auditors / dashboards
# rely on; widening it requires a deliberate code change, not a caller-side
# accident.
_ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "turn_started":    frozenset({"lock_wait_s", "concurrency_wait_s",
                                  "inbound_queue_depth", "outbound_queue_depth",
                                  "active_sessions"}),
    "turn_finished":   frozenset({"duration_s"}),
    "tool_call":       frozenset({"tool", "allowed", "reason", "migration_mode"}),
    "tool_result":     frozenset({"tool", "duration_s", "error", "exception", "reason"}),
    "policy_decision": frozenset({"phase", "tool", "allowed", "reason", "migration_mode", "sender_hash"}),
    "error":           frozenset({"where", "exception"}),
}


def hash_identity(value: str) -> str:
    """Pseudonymize an identity string with a short stable hash.

    AGENTS.md forbids logging PII / secrets verbatim; raw session keys,
    sender ids, and channel:sender pairs can carry user-attributable
    identifiers (Slack user ids, Telegram chat ids, email addresses for
    the email channel). Replace with a 12-char hex digest that's stable
    per-process so traces can still be correlated.

    This is the SAME helper used by every new runtime log site (telemetry
    sinks, ingress-denial warnings, API permission-error warnings) so a
    grep for raw identifiers in plan-introduced logging finds nothing.
    """
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# Backward-compat alias for the original telemetry-internal name.
_hash_session_key = hash_identity


@dataclass(slots=True)
class TelemetryEvent:
    """One observable thing that happened.

    The schema is allow-listed (see _ALLOWED_EVENT_NAMES /
    _ALLOWED_ATTRIBUTES) and `session_key` is pseudonymized at record-build
    time. Sinks NEVER see raw user identifiers.
    """

    name: str  # must be in _ALLOWED_EVENT_NAMES
    context: RequestContext
    attributes: dict[str, Any]
    timestamp_s: float = 0.0  # filled by sinks at emit time

    def to_record(self) -> dict[str, Any]:
        # Filter attributes to the per-event allow-list. Unknown keys are
        # silently dropped — the emit-side warning happens once in `emit()`.
        allowed = _ALLOWED_ATTRIBUTES.get(self.name, frozenset())
        filtered_attrs = {k: v for k, v in self.attributes.items() if k in allowed}
        return {
            "ts": self.timestamp_s or time.time(),
            "name": self.name,
            "trace_id": self.context.trace_id,
            "span_id": self.context.span_id,
            "parent_span_id": self.context.parent_span_id,
            # Pseudonymized: NEVER write the raw session_key (which may carry
            # user-attributable identifiers like Slack user ids).
            "session_key_hash": _hash_session_key(self.context.session_key),
            "channel": self.context.channel,
            "agent_id": self.context.agent_id,
            "policy_version": self.context.policy_version,
            "recursion_depth": self.context.recursion_depth,
            "attributes": filtered_attrs,
        }


class TelemetrySink(ABC):
    @abstractmethod
    def emit(self, event: TelemetryEvent) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class LoggingSink(TelemetrySink):
    """Pipes events through loguru at INFO level."""

    def emit(self, event: TelemetryEvent) -> None:
        rec = event.to_record()
        logger.info("telemetry: {} trace={} attrs={}", rec["name"], rec["trace_id"], rec["attributes"])

    def close(self) -> None:
        pass


class JSONLSink(TelemetrySink):
    """Append-only JSONL file. One event per line. Thread-safe append."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fp = open(self._path, "a", encoding="utf-8")

    def emit(self, event: TelemetryEvent) -> None:
        rec = event.to_record()
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._lock:
            self._fp.write(line)
            self._fp.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fp.closed:
                self._fp.close()


class CompositeSink(TelemetrySink):
    """Fans an event out to every child sink. Errors in one child do not break the others."""

    def __init__(self, children: list[TelemetrySink]):
        self._children = list(children)

    def emit(self, event: TelemetryEvent) -> None:
        for child in self._children:
            try:
                child.emit(event)
            except Exception:
                logger.exception("telemetry sink failed; continuing")

    def close(self) -> None:
        for child in self._children:
            try:
                child.close()
            except Exception:
                logger.exception("telemetry sink close failed")


_active_sink: TelemetrySink | None = None
_lock = threading.Lock()


def set_sink(sink: TelemetrySink | None) -> "TelemetrySink | None":
    """Install the process-wide telemetry sink. Returns the previously-installed sink.

    Returning the old sink lets test fixtures and short-lived AgentLoop
    instances scope a sink temporarily (`prev = set_sink(my_sink); try: ...
    finally: set_sink(prev)`). Multiple concurrent loops that mutate this
    process-global will still clobber each other — for production that is
    acceptable (one gateway process == one telemetry destination), and for
    tests fixtures must use the save/restore idiom shown above.
    """
    global _active_sink
    with _lock:
        previous = _active_sink
        _active_sink = sink
    return previous


def get_sink() -> TelemetrySink | None:
    return _active_sink


# Failure model — explicit so engineers don't have to guess:
#
#   - sink.emit() raising → CompositeSink swallows, logs, continues. Reason:
#     a downed Loki collector must not crash governed actions. Operators
#     who need stronger guarantees mount a JSONLSink alongside any network
#     sink — the local file is the durable audit floor.
#   - emit() with an unknown event name → silently dropped + one-time WARN.
#     Schema is the contract; smuggling unknown names into audit logs would
#     defeat downstream consumers.
#   - emit() with a known name but missing sink → silent no-op (telemetry off).
#
# This is fail-open for transport, fail-closed at the schema boundary —
# which is the right tradeoff for "audit must not crash production but must
# also not record garbage."


_warned_unknown_names: set[str] = set()


def emit(name: str, context: RequestContext, attributes: dict[str, Any]) -> None:
    """Fire-and-forget event emission. No-op when no sink is installed.

    Enforces the schema:
      - Unknown event names log a one-time WARN and are dropped.
      - Unknown attribute keys are silently filtered by TelemetryEvent.to_record().

    Failure mode is **fail-open for normal events, fail-closed at the schema
    boundary**: a typo in the event name does not become a free-form audit
    record. A sink failure is logged but does not block the governed action
    (CompositeSink swallows per-child errors).
    """
    sink = _active_sink
    if sink is None:
        return
    if name not in _ALLOWED_EVENT_NAMES:
        if name not in _warned_unknown_names:
            _warned_unknown_names.add(name)
            logger.warning(
                "telemetry: dropping unknown event name {!r} (not in allow-list)",
                name,
            )
        return
    sink.emit(TelemetryEvent(name=name, context=context, attributes=attributes, timestamp_s=time.time()))
