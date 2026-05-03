"""Minimal policy layer: ingress allow-check + per-tool-call allow-list + budgets.

Scope is intentionally tight (per the user's "two rules well-enforced beat
ten rules half-enforced" guard):

    1. Per-agent allow-lists for tool names. Default agent + per-agent overrides.
    2. Per-turn budgets: max_tool_calls, max_wall_clock_s.
    3. Subagent recursion depth limit.
    4. Optional ingress blocklist by "{channel}:{sender_id}".

What this does NOT do (out of scope, by design): data-scope DSL, escalation
paths, approval workflows, rate-limit windows, model allow-lists. Add those
in a follow-up plan once we have telemetry to show what's actually being hit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pythinker.runtime.context import RequestContext
from pythinker.runtime.telemetry import emit

if TYPE_CHECKING:
    from pythinker.runtime.manifest import AgentRegistry


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


class PolicyService:
    """Synchronous, in-memory policy evaluator.

    `enabled=False` makes every method a pass-through — that's the default
    and matches Pythinker's pre-runtime behaviour.
    """

    _DEFAULT_AGENT = "default"

    _BUILTIN_EXEMPTIONS: dict[str, list[str]] = {
        # Dream consolidation: writes MEMORY.md / SOUL.md / USER.md and reads
        # session history. Anything outside this list is denied even for the
        # system_dream agent — the exemption is narrow and named.
        "system_dream": ["read_file", "edit_file", "write_file"],
    }

    def __init__(
        self,
        *,
        enabled: bool = False,
        allowed_tools: dict[str, list[str]] | None = None,
        blocked_senders: set[str] | None = None,
        max_recursion_depth: int = 3,
        migration_mode: str | None = None,
        builtin_exemptions: dict[str, list[str]] | None = None,
    ) -> None:
        # NOTE: deny-by-default. allowed_tools defaults to {} — when policy
        # is enabled with an empty allow-list, every tool call is denied.
        # Operators who want allow-all during migration must opt in via
        # migration_mode="allow-all".
        self._enabled = enabled
        self._allowed_tools: dict[str, list[str]] = allowed_tools or {}
        self._blocked = set(blocked_senders or [])
        self._max_depth = max_recursion_depth
        self._migration_mode = migration_mode
        # Named exemptions for system-bound agents (e.g. Dream). Override the
        # class default by passing builtin_exemptions; tests rely on this to
        # exercise empty/custom maps. Anything not in this map (and not in
        # _allowed_tools) still falls back to the default agent.
        self._builtin_exemptions = (
            builtin_exemptions if builtin_exemptions is not None
            else dict(self._BUILTIN_EXEMPTIONS)
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def policy_version(self) -> int:
        """Stable for the lifetime of the service. Recreate to bump."""
        return 1 if self._enabled else 0

    def authorize_ingress(self, ctx: RequestContext) -> PolicyDecision:
        if not self._enabled:
            return PolicyDecision(True)
        key = f"{ctx.channel}:{ctx.sender_id}"
        if key in self._blocked:
            # decision.reason flows into telemetry, loop logs, and
            # PermissionError messages that may surface to API clients.
            # Keep it non-identifying. For correlation, emit a
            # separate sender_hash attribute that downstream consumers
            # can join against other hashed log lines.
            from pythinker.runtime.telemetry import hash_identity

            decision = PolicyDecision(False, "sender is blocked")
            emit("policy_decision", ctx, {
                "phase": "ingress",
                "allowed": False,
                "reason": decision.reason,
                "sender_hash": hash_identity(key),
            })
            return decision
        emit("policy_decision", ctx, {"phase": "ingress", "allowed": True})
        return PolicyDecision(True)

    def authorize_tool_call(self, ctx: RequestContext, tool_name: str) -> PolicyDecision:
        if not self._enabled:
            return PolicyDecision(True)

        # 1) Recursion depth — applies regardless of migration mode.
        if ctx.recursion_depth > self._max_depth:
            decision = PolicyDecision(
                False,
                f"recursion depth {ctx.recursion_depth} > {self._max_depth}",
            )
            emit("policy_decision", ctx, {
                "phase": "tool_call", "tool": tool_name,
                "allowed": False, "reason": decision.reason,
            })
            return decision

        # 2) Allow-list check. Migration mode skips ONLY this step — budgets still apply.
        if self._migration_mode != "allow-all":
            if not self._allowed_tools and ctx.agent_id not in self._builtin_exemptions:
                # Deny-default when policy is enabled with no allow-list and no migration mode.
                # System-bound agents (system_dream, …) bypass this branch via _builtin_exemptions
                # so Dream can run even when no manifests are loaded.
                decision = PolicyDecision(False, "no allow-list configured (policy_enabled but empty allowed_tools and no migration_mode)")
                emit("policy_decision", ctx, {
                    "phase": "tool_call", "tool": tool_name,
                    "allowed": False, "reason": decision.reason,
                })
                return decision

            # Explicit-key lookup: a manifest with allowed_tools=[] is "configured empty"
            # and means deny everything for that agent — it must NOT fall back to default.
            # Builtin exemptions (system_dream, …) are consulted only when no manifest
            # claims the agent_id; an explicit manifest always wins.
            if ctx.agent_id in self._allowed_tools:
                allowed = self._allowed_tools[ctx.agent_id]
            elif ctx.agent_id in self._builtin_exemptions:
                allowed = self._builtin_exemptions[ctx.agent_id]
            else:
                allowed = self._allowed_tools.get(self._DEFAULT_AGENT, [])

            if "*" not in allowed and tool_name not in allowed:
                decision = PolicyDecision(False, f"tool {tool_name!r} not in allow-list for agent {ctx.agent_id!r}")
                emit("policy_decision", ctx, {
                    "phase": "tool_call", "tool": tool_name,
                    "allowed": False, "reason": decision.reason,
                })
                return decision

        # 3) Budgets — apply unconditionally when policy is enabled, including
        # under migration_mode. Tool-call-count and wall-clock remain real
        # governed-execution controls; migration mode is an allow-list bypass,
        # not a budget bypass.
        bc = ctx.budgets
        if not bc.is_disabled():
            remaining = bc.debit_tool_call()
            if remaining < 0:
                decision = PolicyDecision(False, f"max_tool_calls budget exhausted ({bc.tool_calls_used}/{bc.max_tool_calls})")
                emit("policy_decision", ctx, {
                    "phase": "tool_call", "tool": tool_name,
                    "allowed": False, "reason": decision.reason,
                })
                return decision
            wall_left = bc.wall_clock_remaining_s()
            if wall_left < 0:
                decision = PolicyDecision(False, "max_wall_clock_s budget exhausted")
                emit("policy_decision", ctx, {
                    "phase": "tool_call", "tool": tool_name,
                    "allowed": False, "reason": decision.reason,
                })
                return decision

        emit("policy_decision", ctx, {
            "phase": "tool_call", "tool": tool_name, "allowed": True,
            **({"migration_mode": "allow-all"} if self._migration_mode == "allow-all" else {}),
        })
        return PolicyDecision(True)

    def tool_name_allowed_by_allowlist(self, ctx: RequestContext, tool_name: str) -> bool:
        """Return whether the allow-list layer would allow ``tool_name``.

        This intentionally ignores budgets and recursion depth. It is used by
        the egress gateway to choose between ``browser.<action>`` and legacy
        plain ``browser`` before making the single authoritative authorization
        call that emits telemetry and debits budgets.
        """
        if not self._enabled or self._migration_mode == "allow-all":
            return True
        if not self._allowed_tools and ctx.agent_id not in self._builtin_exemptions:
            return False
        if ctx.agent_id in self._allowed_tools:
            allowed = self._allowed_tools[ctx.agent_id]
        elif ctx.agent_id in self._builtin_exemptions:
            allowed = self._builtin_exemptions[ctx.agent_id]
        else:
            allowed = self._allowed_tools.get(self._DEFAULT_AGENT, [])
        return "*" in allowed or tool_name in allowed

    def apply_registry(self, registry: "AgentRegistry") -> None:
        """Replace allowed_tools with the per-agent allow-lists from active manifests.

        Manifests with lifecycle in {"draft", "deprecated", "retired"} are
        ignored — only "active" manifests grant tool access. This is the
        single point where the registry becomes load-bearing for policy.
        """
        active: dict[str, list[str]] = {}
        for agent_id in registry.ids():
            manifest = registry.get(agent_id)
            if manifest is None or manifest.lifecycle != "active":
                continue
            active[manifest.id] = list(manifest.allowed_tools)
        self._allowed_tools = active

    def allowed_tools_for(self, agent_id: str) -> list[str]:
        """Inspector for tests + diagnostic logs."""
        return list(self._allowed_tools.get(agent_id, []))

    def active_agent_ids(self) -> list[str]:
        """Sorted ids of agents currently mounted (after apply_registry).

        Used by AgentLoop.__init__ to validate runtime.default_agent_id
        against the registry and fall back when it doesn't match.
        """
        return sorted(self._allowed_tools.keys())
