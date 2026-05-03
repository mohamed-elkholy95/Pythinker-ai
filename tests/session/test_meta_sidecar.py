"""SessionManager meta sidecar — JSON consolidation with legacy `.title` fallback.

Verifies: (a) a freshly-written `<key>.meta.json` round-trips,
(b) a hand-rolled legacy `<key>.title` is read transparently when no JSON
sidecar exists, (c) the first `write_meta` collapses both into a single
`<key>.meta.json` (the orphaned `.title` is harmless), (d) `list_sessions`
surfaces the new `pinned` / `archived` flags.
"""
from pathlib import Path

from pythinker.session.manager import SessionManager


def test_write_then_read_roundtrip(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "hello")
    mgr.save(s)

    mgr.write_meta("websocket:abcd-1234", title="A chat", pinned=True)

    meta = mgr.read_meta("websocket:abcd-1234")
    assert meta == {
        "title": "A chat",
        "pinned": True,
        "archived": False,
        "model_override": None,
    }


def test_legacy_title_sidecar_fallback(tmp_path: Path):
    """A bare `<key>.title` (pre-Phase-4 layout) must read transparently."""
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "hello")
    mgr.save(s)

    legacy = tmp_path / "sessions" / "websocket_abcd-1234.title"
    legacy.write_text("Legacy title", encoding="utf-8")

    meta = mgr.read_meta("websocket:abcd-1234")
    assert meta == {
        "title": "Legacy title",
        "pinned": False,
        "archived": False,
        "model_override": None,
    }


def test_first_write_collapses_legacy(tmp_path: Path):
    """Writing any field promotes legacy state into `<key>.meta.json`."""
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "hello")
    mgr.save(s)
    legacy = tmp_path / "sessions" / "websocket_abcd-1234.title"
    legacy.write_text("Legacy title", encoding="utf-8")

    mgr.write_meta("websocket:abcd-1234", pinned=True)

    meta_path = tmp_path / "sessions" / "websocket_abcd-1234.meta.json"
    assert meta_path.exists()
    meta = mgr.read_meta("websocket:abcd-1234")
    assert meta["title"] == "Legacy title"
    assert meta["pinned"] is True


def test_get_set_title_still_work(tmp_path: Path):
    """Public title API stays compatible with Phase-2 callers."""
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "hello")
    mgr.save(s)

    mgr.set_title("websocket:abcd-1234", "Hello world")

    assert mgr.get_title("websocket:abcd-1234") == "Hello world"


def test_list_sessions_surfaces_pinned_and_archived(tmp_path: Path):
    mgr = SessionManager(workspace=tmp_path)
    s = mgr.get_or_create("websocket:abcd-1234")
    s.add_message("user", "hello")
    mgr.save(s)
    mgr.write_meta("websocket:abcd-1234", pinned=True, archived=False, title="Pinned chat")

    rows = mgr.list_sessions()
    row = next(r for r in rows if r["key"] == "websocket:abcd-1234")
    assert row["pinned"] is True
    assert row["archived"] is False
    assert row["title"] == "Pinned chat"
