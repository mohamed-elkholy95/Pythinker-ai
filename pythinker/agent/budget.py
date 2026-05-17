"""Single source of truth for per-turn token budgets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Zone = Literal["green", "amber", "red", "critical"]


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Per-turn token zones derived from window + output_reserve."""

    window: int
    output_reserve: int
    safety: int
    soft: int
    target: int
    hard: int

    @classmethod
    def for_model(cls, *, window: int, output_reserve: int) -> "BudgetPolicy":
        """Build a policy with clamping for misconfigured model reserves."""
        if window <= 0:
            raise ValueError(f"window must be positive, got {window}")
        try:
            output_reserve = int(output_reserve)
        except (TypeError, ValueError):
            output_reserve = 0
        max_reserve = max(0, window - 1_024)
        output_reserve = min(max(0, output_reserve), max_reserve)
        safety = max(2_048, window // 64)
        safety = min(safety, max(256, (window - output_reserve) // 2))
        soft = max(1, window - output_reserve - 2 * safety)
        target = soft // 2
        hard = max(soft + 1, window - output_reserve - safety)
        return cls(
            window=window,
            output_reserve=output_reserve,
            safety=safety,
            soft=soft,
            target=target,
            hard=hard,
        )

    def classify(self, used: int) -> Zone:
        if used >= self.hard:
            return "critical"
        if used >= self.soft:
            return "red"
        if used >= self.target:
            return "amber"
        return "green"
