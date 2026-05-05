"""Request-context normalization for the agent loop.

Lifted from ``pythinker/agent/loop.py`` so the loop file can stay focused
on lifecycle and dispatch. Pure helpers — agent-id resolution and budget
construction stay on ``AgentLoop`` because they depend on live policy /
runtime-config state; this module receives those values as inputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pythinker.runtime.context import BudgetCounters, RequestContext


def normalize_context(
    *,
    seed: dict[str, str],
    session_key: str,
    agent_id: str,
    policy_version: int,
    budget: "BudgetCounters",
) -> "RequestContext":
    """Build a stamped ``RequestContext`` for one inbound message.

    ``seed`` carries the channel/sender/chat tuple supplied by the call
    site (or pulled from ``InboundMessage.context_seed``); ``agent_id`` and
    ``policy_version`` come from ``AgentLoop._resolve_agent``; ``budget``
    is the per-turn template from ``AgentLoop._budget_template``.
    """
    from pythinker.runtime.context import RequestContext

    ctx = RequestContext.for_inbound(
        channel=seed["channel"],
        sender_id=seed["sender_id"],
        chat_id=seed["chat_id"],
        session_key=session_key,
        budgets=budget,
    )
    return ctx.with_agent_id(agent_id, policy_version=policy_version)


def normalize_context_for_direct(
    *,
    session_key: str,
    agent_id: str,
    policy_version: int,
    budget: "BudgetCounters",
    channel: str = "api",
    sender_id: str = "api-client",
    chat_id: str = "default",
) -> "RequestContext":
    return normalize_context(
        seed={"channel": channel, "sender_id": sender_id, "chat_id": chat_id},
        session_key=session_key,
        agent_id=agent_id,
        policy_version=policy_version,
        budget=budget,
    )


def normalize_context_for_cron(
    *,
    job_id: str,
    session_key: str,
    agent_id: str,
    policy_version: int,
    budget: "BudgetCounters",
) -> "RequestContext":
    return normalize_context(
        seed={"channel": "cron", "sender_id": "system", "chat_id": job_id},
        session_key=session_key,
        agent_id=agent_id,
        policy_version=policy_version,
        budget=budget,
    )


def normalize_context_for_heartbeat(
    *,
    session_key: str,
    agent_id: str,
    policy_version: int,
    budget: "BudgetCounters",
) -> "RequestContext":
    return normalize_context(
        seed={"channel": "heartbeat", "sender_id": "system", "chat_id": "default"},
        session_key=session_key,
        agent_id=agent_id,
        policy_version=policy_version,
        budget=budget,
    )
