"""SessionManager.truncate_after_user_index drops messages strictly after the
given user-turn index, rewrites the JSONL atomically, and raises ValueError
when the requested index does not exist."""
from pathlib import Path

import pytest

from pythinker.session.manager import SessionManager


def test_truncate_drops_assistant_after_user_turn(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "first")
    s.add_message("assistant", "first reply")
    s.add_message("user", "second")
    s.add_message("assistant", "second reply")
    mgr.save(s)

    # Truncate after user_msg_index=1 (the "second" user turn)
    mgr.truncate_after_user_index("websocket:abcd-1234", user_msg_index=1)

    # Drop the in-memory cache so the reload genuinely re-reads from disk —
    # otherwise the assertion would only verify the in-memory mutation.
    mgr.invalidate("websocket:abcd-1234")
    reloaded = mgr.get_or_create("websocket:abcd-1234")
    roles = [m["role"] for m in reloaded.messages]
    contents = [m.get("content") for m in reloaded.messages]
    assert roles == ["user", "assistant", "user"]
    assert contents == ["first", "first reply", "second"]


def test_truncate_at_first_user_drops_only_assistant(tmp_path: Path):
    """Regenerate-from-first-turn use case."""
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "hello")
    s.add_message("assistant", "hi there")
    mgr.save(s)

    mgr.truncate_after_user_index("websocket:abcd-1234", user_msg_index=0)

    mgr.invalidate("websocket:abcd-1234")
    reloaded = mgr.get_or_create("websocket:abcd-1234")
    assert [m["role"] for m in reloaded.messages] == ["user"]


def test_truncate_raises_for_out_of_range_index(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "only turn")
    mgr.save(s)
    with pytest.raises(ValueError, match="no user message at index 1"):
        mgr.truncate_after_user_index("websocket:abcd-1234", user_msg_index=1)
