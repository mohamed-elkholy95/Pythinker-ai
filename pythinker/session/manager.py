"""Session management for conversation history."""

import json
import os
import shutil
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from pythinker.config.paths import get_legacy_sessions_dir
from pythinker.utils.helpers import (
    ensure_dir,
    find_legal_message_start,
    image_placeholder_text,
    safe_filename,
)


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a legal tool-call boundary."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Avoid starting mid-turn when possible.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            content = message.get("content", "")
            # Synthesize an ``[image: path]`` breadcrumb from the persisted
            # ``media`` kwarg so LLM replay still sees *something* where the
            # image used to be. Without this, an image-only user turn
            # replays as an empty user message — the assistant's reply then
            # looks like it's responding to nothing.
            media = message.get("media")
            if isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """Keep a legal recent suffix, mirroring get_history boundary rules."""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        start_idx = max(0, len(self.messages) - max_messages)

        # If the cutoff lands mid-turn, extend backward to the nearest user turn.
        while start_idx > 0 and self.messages[start_idx].get("role") != "user":
            start_idx -= 1

        retained = self.messages[start_idx:]

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, *, cache_max: int = 256):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache_max = max(1, int(cache_max))
        self._cache: "OrderedDict[str, Session]" = OrderedDict()

    @staticmethod
    def safe_key(key: str) -> str:
        """Public helper used by HTTP handlers to map an arbitrary key to a stable filename stem."""
        return safe_filename(key.replace(":", "_"))

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.pythinker/sessions/)."""
        return self.legacy_sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        self._cache.move_to_end(key)
        self._evict_if_full()
        return session

    def load_existing(self, key: str) -> Session | None:
        """Return the session for *key* if it exists in cache or on disk; else None.

        Read-only counterpart to :meth:`get_or_create`. Used by admin tools that
        must distinguish "this session exists" from "this session does not" —
        ``get_or_create`` would silently materialise an empty session for a
        mistyped key and persist it on the next save.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return self._load(key)

    def _evict_if_full(self) -> None:
        while len(self._cache) > self._cache_max:
            evicted_key, _ = self._cache.popitem(last=False)
            logger.debug("SessionManager: evicted {} (cache cap {})", evicted_key, self._cache_max)

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered session {} from corrupt file ({} messages)", key, len(repaired.messages))
            return repaired

    def _repair(self, key: str) -> Session | None:
        """Attempt to recover a session from a corrupt JSONL file."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            skipped = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        if data.get("created_at"):
                            try:
                                created_at = datetime.fromisoformat(data["created_at"])
                            except (ValueError, TypeError):
                                pass
                        if data.get("updated_at"):
                            try:
                                updated_at = datetime.fromisoformat(data["updated_at"])
                            except (ValueError, TypeError):
                                pass
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            if skipped:
                logger.warning("Skipped {} corrupt lines in session {}", skipped, key)

            if not messages and not metadata:
                return None

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Save a session to disk atomically.

        When *fsync* is ``True`` the final file and its parent directory are
        explicitly flushed to durable storage.  This is intentionally off by
        default (the OS page-cache is sufficient for normal operation) but
        should be enabled during graceful shutdown so that filesystems with
        write-back caching (e.g. rclone VFS, NFS, FUSE mounts) do not lose
        the most recent writes.
        """
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated
                }
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())

            os.replace(tmp_path, path)

            if fsync:
                # fsync the directory so the rename is durable.
                # On Windows, opening a directory with O_RDONLY raises
                # PermissionError — skip the dir sync there (NTFS
                # journals metadata synchronously).
                try:
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except PermissionError:
                    pass  # Windows — directory fsync not supported
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._cache[session.key] = session
        self._cache.move_to_end(session.key)
        self._evict_if_full()

    def flush_all(self) -> int:
        """Re-save every cached session with fsync for durable shutdown.

        Returns the number of sessions flushed.  Errors on individual
        sessions are logged but do not prevent other sessions from being
        flushed.
        """
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session {}", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """Remove a session from disk and the in-memory cache.

        Returns True if a JSONL file was found and unlinked.
        """
        path = self._get_session_path(key)
        self.invalidate(key)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.warning("Failed to delete session file {}: {}", path, e)
            return False

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Load a session from disk without caching; intended for read-only HTTP endpoints.

        Returns ``{"key", "created_at", "updated_at", "metadata", "messages"}`` or
        ``None`` when the session file does not exist or fails to parse.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: str | None = None
            updated_at: str | None = None
            stored_key: str | None = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = data.get("created_at")
                        updated_at = data.get("updated_at")
                        stored_key = data.get("key")
                    else:
                        messages.append(data)
            return {
                "key": stored_key or key,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": metadata,
                "messages": messages,
            }
        except Exception as e:
            logger.warning("Failed to read session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session view {} from corrupt file", key)
                return self._session_payload(repaired)
            return None

    def iter_message_files_for_search(self) -> Iterator[tuple[str, list[dict[str, Any]]]]:
        """Yield ``(key, messages)`` for every session JSONL on disk.

        Read-only: deliberately bypasses ``_cache`` and ``get_or_create`` so
        the cross-chat search route cannot resurrect deleted sessions or
        mint empty session files by hammering arbitrary keys (same
        justification as ``_handle_session_usage`` in the websocket
        channel — see ``pythinker/channels/websocket.py:794-799``).

        Corrupt or empty files are skipped with a debug log; iteration
        always completes.
        """
        for path in self.sessions_dir.glob("*.jsonl"):
            fallback_key = path.stem.replace("_", ":", 1)
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if not first_line:
                        continue
                    try:
                        data = json.loads(first_line)
                    except json.JSONDecodeError as exc:
                        logger.debug(
                            "iter_message_files_for_search: bad metadata in {}: {}",
                            path,
                            exc,
                        )
                        continue
                    if data.get("_type") != "metadata":
                        continue
                    key = data.get("key") or fallback_key
                    messages: list[dict[str, Any]] = []
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                    yield key, messages
            except OSError as exc:
                logger.debug(
                    "iter_message_files_for_search: skip {}: {}", path, exc
                )
                continue

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Each entry includes a ``preview`` (first user message, truncated) and
        an optional ``title`` (LLM-generated chat label, persisted as a
        sidecar file) so the WebUI sidebar can show a meaningful chat title
        without having to fetch the full message history per session.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            fallback_key = path.stem.replace("_", ":", 1)
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if not first_line:
                        continue
                    data = json.loads(first_line)
                    if data.get("_type") != "metadata":
                        continue
                    key = data.get("key") or path.stem.replace("_", ":", 1)
                    preview = self._scan_first_user_preview(f)
                    meta = self.read_meta(key)
                    sessions.append({
                        "key": key,
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                        "preview": preview,
                        "title": meta["title"],
                        "pinned": bool(meta["pinned"]),
                        "archived": bool(meta["archived"]),
                        "model_override": meta["model_override"],
                        "path": str(path),
                    })
            except Exception:
                repaired = self._repair(fallback_key)
                if repaired is not None:
                    sessions.append({
                        "key": repaired.key,
                        "created_at": repaired.created_at.isoformat(),
                        "updated_at": repaired.updated_at.isoformat(),
                        "preview": "",
                        "title": "",
                        "pinned": False,
                        "archived": False,
                        "model_override": None,
                        "path": str(path),
                    })
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)

    # ---- Meta sidecar (JSON; absorbs legacy `.title`) ---------------------

    _META_DEFAULT: "dict[str, Any]" = {
        "title": "",
        "pinned": False,
        "archived": False,
        "model_override": None,
    }

    def _meta_sidecar_path(self, key: str) -> Path:
        return self.sessions_dir / f"{self.safe_key(key)}.meta.json"

    def _legacy_title_sidecar_path(self, key: str) -> Path:
        return self.sessions_dir / f"{self.safe_key(key)}.title"

    def read_meta(self, key: str) -> dict[str, Any]:
        """Return ``{title, pinned, archived, model_override}`` for *key*.

        Falls back to the legacy ``<key>.title`` sidecar when no JSON sidecar
        exists. Returns the default dict (all empty/false) when neither file
        is present.
        """
        meta = dict(self._META_DEFAULT)
        json_path = self._meta_sidecar_path(key)
        if json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for k in meta:
                        if k in payload:
                            meta[k] = payload[k]
                return meta
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("read_meta: corrupt sidecar for {}: {}", key, exc)
                # Fall through to legacy read so a corrupt JSON file doesn't
                # erase a working legacy title.
        legacy_path = self._legacy_title_sidecar_path(key)
        try:
            meta["title"] = legacy_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            pass
        return meta

    def write_meta(self, key: str, **fields: Any) -> dict[str, Any]:
        """Merge *fields* into the meta sidecar and persist atomically.

        Unknown fields are silently dropped. Returns the merged meta dict.
        """
        merged = self.read_meta(key)
        for k, v in fields.items():
            if k in merged:
                merged[k] = v
        path = self._meta_sidecar_path(key)
        tmp = path.with_suffix(".meta.json.tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("write_meta: failed for {}: {}", key, exc)
            tmp.unlink(missing_ok=True)
        return merged

    def get_title(self, key: str) -> str:
        return self.read_meta(key).get("title", "") or ""

    def set_title(self, key: str, title: str) -> None:
        title = (title or "").strip()
        if not title:
            return
        self.write_meta(key, title=title)

    def truncate_after_user_index(self, key: str, user_msg_index: int) -> None:
        """Drop every message strictly after the *user_msg_index*-th user message.

        Used by the WebUI's regenerate / edit flows: keep the conversation up to
        and including the target user turn, then drop everything that came after
        so the agent can re-process from that point.

        Raises ``ValueError`` if there is no user message at the given index.
        """
        session = self.get_or_create(key)
        cutoff: int | None = None
        seen_users = -1
        for i, m in enumerate(session.messages):
            if m.get("role") == "user":
                seen_users += 1
                if seen_users == user_msg_index:
                    cutoff = i + 1  # keep this user turn; drop everything after
                    break
        if cutoff is None:
            raise ValueError(
                f"truncate_after_user_index: no user message at index {user_msg_index} "
                f"in session {key!r}"
            )
        session.messages = session.messages[:cutoff]
        session.updated_at = datetime.now()
        self.save(session)

    @staticmethod
    def _scan_first_user_preview(fp: Any, *, max_lines: int = 64, max_chars: int = 120) -> str:
        """Return the first user-role message text in *fp*, truncated.

        Scans at most ``max_lines`` lines past the cursor so listing remains
        fast on long histories. Returns an empty string if no user message is
        found within the scan budget.
        """
        for _ in range(max_lines):
            line = fp.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            text = content.replace("\n", " ").strip()
            if not text:
                continue
            return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
        return ""
