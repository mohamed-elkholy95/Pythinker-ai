"""Turn-persistence + message sanitization for the agent loop.

`TurnWriter` owns the path from runner output → session JSONL rows. Two
responsibilities:

1. **save_turn(session, messages, skip)** — append per-role messages from a
   completed turn into `session.messages`, stripping volatile context (the
   runtime-context block prepended to the initial user message) and
   truncating oversized tool results to keep history readable.

2. **persist_subagent_followup(session, msg)** — fold subagent results into
   session history early so the durable view stays consistent even if the
   parent turn crashes before save_turn runs. Dedup on `subagent_task_id`.

Extracted from `AgentLoop` so the persistence + sanitization logic has clear
test boundaries. `AgentLoop` keeps thin delegate methods so external callers
and existing test patches keep working.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pythinker.agent.context import ContextBuilder
from pythinker.utils.helpers import image_placeholder_text
from pythinker.utils.helpers import truncate_text as truncate_text_fn

if TYPE_CHECKING:
    from pythinker.agent.checkpoint import CheckpointManager
    from pythinker.bus.events import InboundMessage
    from pythinker.session.manager import Session, SessionManager


class TurnWriter:
    """Persist completed-turn messages + subagent follow-ups to session history.

    Holds references to the session manager and checkpoint manager so
    `persist_user_message_early` can both save the session and mark the
    pending-user-turn flag in one call.
    """

    def __init__(
        self,
        sessions: "SessionManager",
        checkpoint: "CheckpointManager",
        max_tool_result_chars: int,
    ) -> None:
        self.sessions = sessions
        self._checkpoint = checkpoint
        self.max_tool_result_chars = max_tool_result_chars

    def sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def save_turn(
        self,
        session: "Session",
        messages: list[dict],
        skip: int,
        *,
        turn_latency_ms: int | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results.

        When ``turn_latency_ms`` is provided, stamps it as ``latency_ms`` on
        the last assistant message appended in this call. No-op for turns
        that did not append an assistant row (e.g. tool-only continuations).
        """
        last_assistant_idx: int | None = None
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self.sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the entire runtime-context block (including any session summary).
                    # The block is bounded by _RUNTIME_CONTEXT_TAG and _RUNTIME_CONTEXT_END.
                    end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                    end_pos = content.find(end_marker)
                    if end_pos >= 0:
                        after = content[end_pos + len(end_marker):].lstrip("\n")
                        if after:
                            entry["content"] = after
                        else:
                            continue
                    else:
                        # Fallback: no end marker found, strip the tag prefix
                        after_tag = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG):].lstrip("\n")
                        if after_tag.strip():
                            entry["content"] = after_tag
                        else:
                            continue
                if isinstance(content, list):
                    filtered = self.sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
            if role == "assistant":
                last_assistant_idx = len(session.messages) - 1
        if turn_latency_ms is not None and last_assistant_idx is not None:
            session.messages[last_assistant_idx]["latency_ms"] = int(turn_latency_ms)
        session.updated_at = datetime.now()

    def persist_user_message_early(
        self,
        msg: "InboundMessage",
        session: "Session",
        **kwargs: Any,
    ) -> bool:
        """Persist the triggering user message before the turn starts.

        Writes the user message immediately, marks the pending-user-turn flag
        (so a mid-turn crash can synthesize an "interrupted" assistant reply
        on recovery), and saves the session. Extra keyword arguments are
        merged onto the persisted row (e.g. ``_command=True`` for slash-command
        turns persisted outside the LLM flow).

        Returns True if a row was persisted, False if the message had neither
        text nor media (nothing to save).
        """
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if not (has_text or media_paths):
            return False

        extra: dict[str, Any] = {"media": list(media_paths)} if media_paths else {}
        extra.update(kwargs)
        text = msg.content if isinstance(msg.content, str) else ""
        session.add_message("user", text, **extra)
        self._checkpoint.mark_pending_user_turn(session)
        self.sessions.save(session)
        return True

    def persist_subagent_followup(self, session: "Session", msg: "InboundMessage") -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True
