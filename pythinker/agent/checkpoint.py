"""Mid-turn recovery: persist/restore in-flight turn state across crashes.

`CheckpointManager` owns the two pieces of session metadata used to survive
interrupted turns:

- ``runtime_checkpoint``: snapshot of an assistant message + completed/pending
  tool results, written by the agent loop after each tool burst. On the next
  request, the checkpoint is folded back into ``session.messages`` so the
  history is consistent before a new user turn starts.

- ``pending_user_turn``: a flag indicating a user message was persisted but no
  assistant reply was ever written (typically a crash or cancellation between
  the user-message save and the first model call). The next request closes
  the turn with an "interrupted" assistant message so the history stays
  coherent.

Extracted from ``AgentLoop`` so the checkpoint/restore logic has clear test
boundaries. ``AgentLoop`` keeps thin delegate methods (and re-exports the key
constants) so external callers and the existing test patches keep working.

Per AGENTS.md: the literal string values ``"runtime_checkpoint"`` and
``"pending_user_turn"`` must never change — live sessions depend on them for
mid-turn recovery across restarts.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pythinker.session.manager import SessionManager
    from pythinker.session.session import Session


RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
PENDING_USER_TURN_KEY = "pending_user_turn"


def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
    return (
        message.get("role"),
        message.get("content"),
        message.get("tool_call_id"),
        message.get("name"),
        message.get("tool_calls"),
        message.get("reasoning_content"),
        message.get("thinking_blocks"),
    )


class CheckpointManager:
    """Owns runtime-checkpoint and pending-user-turn session metadata.

    Stateless beyond an injected SessionManager (used to persist after writes).
    """

    RUNTIME_CHECKPOINT_KEY = RUNTIME_CHECKPOINT_KEY
    PENDING_USER_TURN_KEY = PENDING_USER_TURN_KEY

    def __init__(self, sessions: "SessionManager") -> None:
        self.sessions = sessions

    def set_runtime_checkpoint(self, session: "Session", payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self.RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def clear_runtime_checkpoint(self, session: "Session") -> None:
        if self.RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self.RUNTIME_CHECKPOINT_KEY, None)

    def mark_pending_user_turn(self, session: "Session") -> None:
        session.metadata[self.PENDING_USER_TURN_KEY] = True

    def clear_pending_user_turn(self, session: "Session") -> None:
        session.metadata.pop(self.PENDING_USER_TURN_KEY, None)

    def restore_runtime_checkpoint(self, session: "Session") -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        checkpoint = session.metadata.get(self.RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                _checkpoint_message_key(left) == _checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self.clear_pending_user_turn(session)
        self.clear_runtime_checkpoint(session)
        return True

    def restore_pending_user_turn(self, session: "Session") -> bool:
        """Close a turn that only persisted the user message before crashing."""
        if not session.metadata.get(self.PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self.clear_pending_user_turn(session)
        return True
