"""OverlayContainer: push/pop/visible."""
from __future__ import annotations


def test_overlay_push_pop_visibility() -> None:
    from pythinker.cli.tui.panes.overlay import OverlayContainer, OverlayScreen

    class _Stub(OverlayScreen):
        def render(self): return [("", "stub")]
        def handle_key(self, key) -> bool: return False

    o = OverlayContainer()
    assert not o.visible
    o.push(_Stub())
    assert o.visible
    o.pop()
    assert not o.visible
