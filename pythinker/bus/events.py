"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pythinker.runtime.context import RequestContext


@dataclass
class InboundMessage:
    """Message received from a chat channel.

    `context_seed`: identity hints supplied by whoever produced the message
    (channel, direct API, cron). The full RequestContext is built later, in
    AgentLoop._normalize_context, so every inbound path goes through one
    code path and stamps the same budgets/agent_id/policy_version.

    `context`: populated by AgentLoop after _normalize_context runs. None
    when the message has not yet been picked up by the loop.
    """

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    context_seed: dict[str, str] | None = None  # {channel, sender_id, chat_id}
    context: "RequestContext | None" = None  # Filled by AgentLoop._normalize_context

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


