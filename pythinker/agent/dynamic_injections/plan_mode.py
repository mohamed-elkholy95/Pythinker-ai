"""Plan-mode reminder injector.

Audit §4 Phase 5. Pythinker has no ``EnterPlanMode`` / ``ExitPlanMode``
tools today, so this provider is a scaffolding implementation: it reads
``${workspace}/.pythinker/plan.md`` (created by the user / wizard) and,
when present, periodically re-injects the plan as a reminder so plan
discipline survives long sessions.

Cadence: full plan on the first turn after enable, then sparse "stay
on plan" reminders every ``sparse_every`` turns. The provider tracks
its own iteration counter per-session so multiple concurrent sessions
don't share state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pythinker.agent.dynamic_injection import DynamicInjection, DynamicInjectionProvider

_FULL_REMINDER_HEADER = "## Active plan (re-injected for context)"
_SPARSE_REMINDER = (
    "Reminder: a plan is active for this session — "
    "stay on the numbered steps in `${workspace}/.pythinker/plan.md`. "
    "If the plan needs to change, surface the change before deviating."
)


class PlanModeProvider(DynamicInjectionProvider):
    """Re-inject ``${workspace}/.pythinker/plan.md`` on a cadence.

    No-op when the plan file does not exist — operators opt in by
    writing the file (or having ``/init`` write it for them).
    """

    def __init__(
        self,
        workspace: Path,
        *,
        sparse_every: int = 5,
        max_full_chars: int = 4000,
    ) -> None:
        self._plan_path = Path(workspace) / ".pythinker" / "plan.md"
        self._sparse_every = max(1, sparse_every)
        self._max_full_chars = max(256, max_full_chars)
        # Per-session iteration counter — keys = session_key, values = counter.
        self._counter: dict[str, int] = {}

    def get_injections(
        self,
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        session_key: str | None = None,
    ) -> list[DynamicInjection]:
        if not self._plan_path.is_file():
            return []

        key = session_key or "__default__"
        n = self._counter.get(key, 0)
        self._counter[key] = n + 1

        if n == 0:
            try:
                body = self._plan_path.read_text(encoding="utf-8")
            except OSError:
                return []
            if not body.strip():
                return []
            if len(body) > self._max_full_chars:
                body = body[: self._max_full_chars] + "\n\n[plan truncated for prompt budget]"
            return [
                DynamicInjection(
                    content=f"{_FULL_REMINDER_HEADER}\n\n{body}",
                    role="system",
                    placement="append",
                    metadata={"injection": "plan_mode", "kind": "full"},
                )
            ]

        if n % self._sparse_every == 0:
            return [
                DynamicInjection(
                    content=_SPARSE_REMINDER,
                    role="system",
                    placement="append",
                    metadata={"injection": "plan_mode", "kind": "sparse"},
                )
            ]

        return []
