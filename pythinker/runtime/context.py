"""RequestContext: identity + trace + budgets carried through every hop.

The single anchor that telemetry, policy, egress, and manifests all attach to.
Constructed once per inbound path inside AgentLoop._normalize_context (or one
of its _normalize_context_for_* siblings for direct/cron/heartbeat paths) and
forwarded unchanged through AgentRunner → tools. Channels and direct-API
entrypoints supply only seed identity (channel, sender_id, chat_id) on
InboundMessage.context_seed; the loop produces the full context.

Subagents get a derived child context that shares trace_id but carries an
incremented recursion_depth so the policy layer can bound A2A storms.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace


@dataclass(slots=True)
class BudgetCounters:
    """Per-turn enforcement budgets. A 0/0.0 value means the budget is disabled."""

    max_tool_calls: int = 0
    max_wall_clock_s: float = 0.0
    started_at_monotonic: float = field(default_factory=time.monotonic)
    tool_calls_used: int = 0

    def is_disabled(self) -> bool:
        return self.max_tool_calls <= 0 and self.max_wall_clock_s <= 0.0

    def debit_tool_call(self) -> int:
        """Spend one tool call and return remaining (negative means exhausted).

        When max_tool_calls <= 0 the budget is disabled and this always returns 0.
        Callers check the sign to decide whether to deny the call.
        """
        if self.max_tool_calls <= 0:
            return 0
        self.tool_calls_used += 1
        return self.max_tool_calls - self.tool_calls_used

    def wall_clock_remaining_s(self) -> float:
        """Return remaining wall-clock seconds (negative means exhausted)."""
        if self.max_wall_clock_s <= 0.0:
            return float("inf")
        return self.max_wall_clock_s - (time.monotonic() - self.started_at_monotonic)


@dataclass(slots=True)
class RequestContext:
    """Trace + identity + budgets for one in-flight request.

    Created at channel ingress; immutable except for the `budgets` field
    (counters mutate in place). Helper methods return new instances rather
    than mutate identity fields.
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    session_key: str
    channel: str
    sender_id: str
    chat_id: str
    agent_id: str = "default"
    policy_version: int = 0
    recursion_depth: int = 0
    budgets: BudgetCounters = field(default_factory=BudgetCounters)

    @classmethod
    def for_inbound(
        cls,
        *,
        channel: str,
        sender_id: str,
        chat_id: str,
        session_key: str,
        budgets: BudgetCounters | None = None,
    ) -> "RequestContext":
        """Build a fresh top-level context for an inbound message."""
        return cls(
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=None,
            session_key=session_key,
            channel=channel,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            budgets=budgets or BudgetCounters(),
        )

    def with_agent_id(self, agent_id: str, *, policy_version: int = 0) -> "RequestContext":
        """Return a copy bound to a specific agent + policy version."""
        return replace(self, agent_id=agent_id, policy_version=policy_version)

    def child_for_subagent(self, *, label: str) -> "RequestContext":
        """Return a child context for a spawned subagent.

        Same trace_id, new span_id, parent_span_id pointing at us,
        recursion_depth incremented. `label` is informational (recorded by
        callers in telemetry) and intentionally not stored on the context.
        """
        del label  # documented; consumers attach in their own emit() calls
        return replace(
            self,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=self.span_id,
            recursion_depth=self.recursion_depth + 1,
            # Subagents share the parent budget object so a runaway tree
            # is bounded by the same total tool-call ceiling.
            budgets=self.budgets,
        )
