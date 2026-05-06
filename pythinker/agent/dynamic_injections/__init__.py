"""Bundled :class:`DynamicInjectionProvider` implementations.

Phase 5 of `.agents/plans/2026-05-05-coding-prompt-uplift.md`. Each
provider in this package owns its own throttle / cadence policy; the
runner just calls the abstract ``get_injections`` hook.
"""

from pythinker.agent.dynamic_injections.afk_mode import AfkModeProvider
from pythinker.agent.dynamic_injections.plan_mode import PlanModeProvider

__all__ = ["AfkModeProvider", "PlanModeProvider"]
