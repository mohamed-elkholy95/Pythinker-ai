"""Tests for SessionManager.load_existing — read-only counterpart to get_or_create."""

from __future__ import annotations

from pathlib import Path

from pythinker.session.manager import Session, SessionManager


def test_load_existing_returns_none_for_unknown_key(tmp_path: Path) -> None:
    sm = SessionManager(tmp_path)
    assert sm.load_existing("does-not-exist") is None


def test_load_existing_does_not_create_or_save(tmp_path: Path) -> None:
    sm = SessionManager(tmp_path)
    sm.load_existing("phantom-key")
    # No file written, no cache entry left behind.
    sessions_dir = tmp_path / "sessions"
    if sessions_dir.exists():
        assert not (sessions_dir / "phantom-key.jsonl").exists()
    assert "phantom-key" not in sm._cache


def test_load_existing_returns_persisted_session(tmp_path: Path) -> None:
    sm = SessionManager(tmp_path)
    s = Session(key="real:session")
    s.add_message("user", "hello")
    sm.save(s)

    # Drop the in-memory cache to force a disk read.
    sm._cache.clear()

    loaded = sm.load_existing("real:session")
    assert loaded is not None
    assert loaded.key == "real:session"
    assert any(m.get("content") == "hello" for m in loaded.messages)


def test_load_existing_returns_cached_session(tmp_path: Path) -> None:
    sm = SessionManager(tmp_path)
    s = sm.get_or_create("cached:session")
    s.add_message("user", "hi")

    loaded = sm.load_existing("cached:session")
    assert loaded is s, "must return the same cached instance, not reload from disk"
