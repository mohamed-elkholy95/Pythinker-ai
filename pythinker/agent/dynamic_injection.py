"""Dynamic message injection — Phase 5 of the coding-prompt uplift.

A ``DynamicInjectionProvider`` is consulted by the agent runner before
each LLM call. It returns zero-or-more ``DynamicInjection`` records,
which the runner prepends to the message list as ``user``-role messages
(or appends as ``system`` reminders, depending on the injection's
``placement``).

Design intent: a single hook point lets us layer plan-mode reminders,
AFK-mode signals, and similar "context that shouldn't live in the
persistent history" without scattering policy logic across the runner.
The runner does not interpret injection content — providers own all
policy. Default `None` provider in :class:`~pythinker.agent.runner.AgentRunSpec`
keeps the legacy behavior unchanged.

See ``.agents/plans/2026-05-05-coding-prompt-uplift.md`` §4 Phase 5.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class DynamicInjection:
    """One injected message produced by a :class:`DynamicInjectionProvider`."""

    content: str
    role: Literal["system", "user"] = "user"
    # ``prepend`` puts the injection before the trailing user message;
    # ``append`` puts it after (so it's the last thing the model sees).
    placement: Literal["prepend", "append"] = "append"
    metadata: dict[str, Any] = field(default_factory=dict)


class DynamicInjectionProvider(ABC):
    """Abstract source of per-iteration injections.

    Subclasses must be cheap to call — the runner consults them on every
    iteration and any latency lands directly on turn time.
    """

    @abstractmethod
    def get_injections(
        self,
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        session_key: str | None = None,
    ) -> list[DynamicInjection]:
        """Return zero-or-more injections for the upcoming model call.

        ``messages`` is the prepared message list (post-governance), so a
        provider can inspect history depth, last user content, etc.
        ``iteration`` is the 0-indexed iteration count within the current
        turn — useful for cadence-based throttles.

        Implementations should return an empty list when the injection is
        not yet due rather than raising.
        """
        raise NotImplementedError


class CompositeInjectionProvider(DynamicInjectionProvider):
    """Glue multiple providers behind one hook so the runner sees a single seam."""

    def __init__(self, providers: list[DynamicInjectionProvider]) -> None:
        self._providers = list(providers)

    def get_injections(
        self,
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        session_key: str | None = None,
    ) -> list[DynamicInjection]:
        out: list[DynamicInjection] = []
        for p in self._providers:
            try:
                out.extend(p.get_injections(messages, iteration=iteration, session_key=session_key))
            except Exception:
                # A buggy provider must not break the turn. Drop its
                # contribution and continue.
                continue
        return out


def apply_injections(
    messages: list[dict[str, Any]],
    injections: list[DynamicInjection],
) -> list[dict[str, Any]]:
    """Return a new message list with ``injections`` woven in.

    ``placement="append"`` injections land at the end of the list (last
    thing the model sees); ``placement="prepend"`` injections land
    immediately before the trailing user message (or at the front if
    there's no user message yet).
    """
    if not injections:
        return messages

    appended = [
        {"role": inj.role, "content": inj.content}
        for inj in injections
        if inj.placement == "append"
    ]
    prepended = [
        {"role": inj.role, "content": inj.content}
        for inj in injections
        if inj.placement == "prepend"
    ]

    if not prepended:
        return list(messages) + appended

    last_user_idx = next(
        (i for i in reversed(range(len(messages))) if messages[i].get("role") == "user"),
        None,
    )
    if last_user_idx is None:
        return prepended + list(messages) + appended
    return (
        list(messages[:last_user_idx])
        + prepended
        + list(messages[last_user_idx:])
        + appended
    )
