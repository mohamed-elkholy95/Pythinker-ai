"""SessionManager._cache enforces an LRU cap."""

from pathlib import Path

from pythinker.session.manager import SessionManager


def test_cache_evicts_oldest_when_cap_reached(tmp_path: Path):
    sm = SessionManager(workspace=tmp_path, cache_max=3)
    a = sm.get_or_create("a:1")
    b = sm.get_or_create("b:1")
    c = sm.get_or_create("c:1")
    assert set(sm._cache.keys()) == {"a:1", "b:1", "c:1"}
    sm.save(a)
    sm.save(b)
    sm.save(c)
    sm.get_or_create("d:1")  # fourth -> evicts oldest ("a:1")
    assert "a:1" not in sm._cache
    assert "d:1" in sm._cache


def test_get_or_create_touches_recency(tmp_path: Path):
    sm = SessionManager(workspace=tmp_path, cache_max=3)
    sm.get_or_create("a:1")
    sm.get_or_create("b:1")
    sm.get_or_create("c:1")
    sm.get_or_create("a:1")  # touch — should now be most-recent
    sm.get_or_create("d:1")  # evicts oldest, which is now "b:1" (not "a:1")
    assert "b:1" not in sm._cache
    assert "a:1" in sm._cache


def test_default_cap_keeps_existing_behaviour(tmp_path: Path):
    sm = SessionManager(workspace=tmp_path)
    # Default cap is 256 — adding 10 should not evict anything.
    for i in range(10):
        sm.get_or_create(f"k:{i}")
    assert len(sm._cache) == 10
