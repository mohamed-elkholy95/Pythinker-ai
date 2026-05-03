"""SessionManager.iter_message_files_for_search yields (key, messages) tuples
for every session JSONL on disk WITHOUT touching the in-memory cache.

The cross-chat search route walks every session; using the cached path or
``get_or_create`` would silently resurrect deleted sessions and let any
authenticated caller mint empty session files by hammering arbitrary keys.
This generator mirrors the read-only pattern of ``read_session_file``.
"""
from pathlib import Path

from pythinker.session.manager import SessionManager


def test_iter_yields_each_session_once(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    a = mgr.get_or_create("websocket:abcd-1")
    a.add_message("user", "hello")
    mgr.save(a)
    b = mgr.get_or_create("websocket:efgh-2")
    b.add_message("user", "world")
    mgr.save(b)

    rows = sorted(mgr.iter_message_files_for_search())
    assert [k for k, _ in rows] == ["websocket:abcd-1", "websocket:efgh-2"]
    assert rows[0][1][0]["content"] == "hello"


def test_iter_skips_corrupt_files(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1")
    s.add_message("user", "hello")
    mgr.save(s)
    # Drop a garbage file alongside the real one.
    (tmp_path / "sessions" / "websocket_broken.jsonl").write_text(
        "{not json", encoding="utf-8",
    )

    rows = list(mgr.iter_message_files_for_search())
    keys = [k for k, _ in rows]
    assert "websocket:abcd-1" in keys
    # Broken file must not crash the generator; it may be skipped or yield
    # an empty message list — either is acceptable, but the iteration must
    # complete.
    assert all(isinstance(msgs, list) for _, msgs in rows)


def test_iter_does_not_populate_cache(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1")
    s.add_message("user", "hello")
    mgr.save(s)
    mgr.invalidate("websocket:abcd-1")

    list(mgr.iter_message_files_for_search())

    assert "websocket:abcd-1" not in mgr._cache
