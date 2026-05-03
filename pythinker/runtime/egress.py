"""Single chokepoint around tool execution.

Every tool call from the agent runner goes through `execute(ctx, name, params)`.
The gateway:

    1. Calls the policy service (allow-list, budgets, recursion depth).
    2. Emits `tool_call` and `tool_result` telemetry on every path
       (allowed, denied, succeeded, failed).
    3. Delegates the actual invocation to the existing ToolRegistry.execute()
       so the prepare_call/cast/validate/error-hint behaviour is preserved.

This is the only file that knows the wiring between policy and the registry.
"""

from __future__ import annotations

import time
from typing import Any

from pythinker.agent.tools.registry import ToolRegistry
from pythinker.runtime.context import RequestContext
from pythinker.runtime.policy import PolicyService
from pythinker.runtime.telemetry import emit

_DENY_HINT = "\n\n[Analyze the error above and try a different approach.]"


class ToolEgressGateway:
    def __init__(self, *, registry: ToolRegistry, policy: PolicyService):
        self._registry = registry
        self._policy = policy

    def _policy_name(self, name: str, params: dict[str, Any]) -> str:
        if name == "browser":
            action = params.get("action")
            if isinstance(action, str) and action.strip():
                return f"browser.{action.strip()}"
        return name

    def _authorize_name(self, ctx: RequestContext, name: str, params: dict[str, Any]) -> str:
        policy_name = self._policy_name(name, params)
        if (
            policy_name != name
            and not self._policy.tool_name_allowed_by_allowlist(ctx, policy_name)
        ):
            return name
        return policy_name

    async def execute(
        self,
        ctx: RequestContext,
        name: str,
        params: dict[str, Any],
    ) -> Any:
        policy_name = self._authorize_name(ctx, name, params)
        decision = self._policy.authorize_tool_call(ctx, policy_name)
        if not decision.allowed:
            # Audit completeness: denied calls emit BOTH a tool_call (with
            # allowed=False) AND a terminal tool_result (with error=True).
            # Without the terminal event, downstream consumers that pair
            # tool_call/tool_result records to compute durations or detect
            # in-flight tools would see denied calls as "still running".
            emit("tool_call", ctx, {"tool": policy_name, "allowed": False, "reason": decision.reason})
            emit("tool_result", ctx, {
                "tool": policy_name, "duration_s": 0.0, "error": True, "reason": decision.reason,
            })
            # Prefix "Error: " so AgentRunner._run_tool's
            # `result.startswith("Error")` detector flips this to
            # status="error". Without the prefix, the runner records the
            # denied call as status="ok", fail_on_tool_error never trips,
            # and downstream callers treat policy rejections as successful
            # work — silently weakening enforcement.
            return f"Error: Policy denied: {decision.reason}{_DENY_HINT}"
        emit("tool_call", ctx, {"tool": policy_name, "allowed": True})
        started = time.monotonic()
        try:
            result = await self._registry.execute(name, params)
            duration_s = time.monotonic() - started
            is_error = isinstance(result, str) and result.startswith("Error")
            emit(
                "tool_result", ctx,
                {"tool": policy_name, "duration_s": duration_s, "error": bool(is_error)},
            )
            return result
        except Exception as exc:
            duration_s = time.monotonic() - started
            emit(
                "tool_result", ctx,
                {
                    "tool": policy_name,
                    "duration_s": duration_s,
                    "error": True,
                    "exception": type(exc).__name__,
                },
            )
            return f"Error executing {policy_name}: {exc}{_DENY_HINT}"
