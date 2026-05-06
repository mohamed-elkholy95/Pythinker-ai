"""AFK-mode injector.

Audit §4 Phase 5. AFK = "no human listening on this channel right now"
(heartbeat tick, cron job, scheduled message). When AFK, the agent
should not gate on AskUser-style flows — there's no one to answer. This
injector marks the situation in a single system message so downstream
prompts know to lean toward "make the best decision and proceed."
"""

from __future__ import annotations

from typing import Any

from pythinker.agent.dynamic_injection import DynamicInjection, DynamicInjectionProvider

_AFK_REMINDER = (
    "AFK mode: no human is listening on this channel right now (heartbeat / "
    "cron / scheduled message). Do not stall waiting for clarification. Make "
    "the best decision the available context supports and proceed; surface "
    "the assumption you took in your final response."
)


class AfkModeProvider(DynamicInjectionProvider):
    """Inject the AFK reminder once per turn for non-interactive channels.

    ``afk_channels`` defaults to ``{"heartbeat", "cron", "scheduled"}`` —
    the synthetic-origin channels Pythinker uses for unattended ticks.
    Operators can override the set.
    """

    _DEFAULT_CHANNELS = frozenset({"heartbeat", "cron", "scheduled"})

    def __init__(self, afk_channels: set[str] | None = None) -> None:
        self._channels = (
            frozenset(afk_channels) if afk_channels is not None else self._DEFAULT_CHANNELS
        )

    def get_injections(
        self,
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        session_key: str | None = None,
    ) -> list[DynamicInjection]:
        # Only emit on the first iteration of a turn — repeating across
        # the iteration loop would just burn tokens; the first one
        # already lives in the prompt for the rest of the turn.
        if iteration != 0:
            return []
        if not session_key:
            return []
        # session_key is "<channel>:<chat_id>"; pull the channel off.
        channel = session_key.split(":", 1)[0]
        if channel not in self._channels:
            return []
        return [
            DynamicInjection(
                content=_AFK_REMINDER,
                role="system",
                placement="append",
                metadata={"injection": "afk_mode"},
            )
        ]
