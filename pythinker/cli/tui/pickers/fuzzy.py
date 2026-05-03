"""Substring fuzzy matcher for picker overlays.

Pythinker has no existing fuzzy helper; this is the in-package
implementation. Sort key: (does_match desc, starts_with desc, position asc,
length asc, alphabetical asc) — prefix wins, then earliest match position,
then shorter strings, then alphabetical as a deterministic tie-break.
Case-insensitive throughout.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Generic, Iterable, TypeVar

from pythinker.cli.tui.panes.overlay import OverlayScreen

T = TypeVar("T")


def fuzzy_match(query: str, candidates: Iterable[T]) -> list[tuple[T, int]]:
    """Return ``[(item, score), ...]`` sorted best-match-first.

    With an empty query, returns the input order unchanged (each scored 0).
    With a non-empty query, drops items whose lowercased form does not
    contain the lowercased query.
    """
    items = list(candidates)
    if not query:
        return [(item, 0) for item in items]

    q = query.lower()

    scored: list[tuple[int, int, int, int, str, T]] = []
    for item in items:
        text = str(item).lower()
        idx = text.find(q)
        if idx < 0:
            continue
        starts_with = 1 if idx == 0 else 0
        scored.append((
            -starts_with,
            idx,
            len(text),
            0,
            text,             # alphabetical tie-break
            item,
        ))

    scored.sort(key=lambda row: (row[0], row[1], row[2], row[4]))
    return [(item, -row[0]) for row in scored for item in [row[5]]]


class FuzzyPickerScreen(OverlayScreen, Generic[T]):
    """Filterable picker overlay. Items are arbitrary objects; ``label_fn``
    converts each to the display + filter string.

    Labels are precomputed once at construction time so ``visible_items``
    and ``move_cursor`` stay O(n) even for large item lists.
    """

    def __init__(
        self,
        items: list[T],
        *,
        label_fn: Callable[[T], str],
        on_select: Callable[[T], Awaitable[None]],
        title: str = "",
        max_visible: int = 14,
    ) -> None:
        # Pre-compute (item, label) pairs once so every query is O(n).
        self._labeled: list[tuple[T, str]] = [(it, label_fn(it)) for it in items]
        self._on_select = on_select
        self._title = title
        self._query = ""
        self._cursor = 0
        # Scroll window: render at most ``max_visible`` items at a time. The
        # cursor stays inside the window — when it would step outside, the
        # offset advances to follow it.
        self._max_visible = max(3, max_visible)
        self._scroll_offset = 0

    def set_query(self, q: str) -> None:
        self._query = q
        self._cursor = 0
        self._scroll_offset = 0

    def move_cursor(self, delta: int) -> None:
        n = len(self._filter())
        if not n:
            return
        self._cursor = max(0, min(n - 1, self._cursor + delta))
        # Keep cursor inside the scroll window.
        if self._cursor < self._scroll_offset:
            self._scroll_offset = self._cursor
        elif self._cursor >= self._scroll_offset + self._max_visible:
            self._scroll_offset = self._cursor - self._max_visible + 1
        max_offset = max(0, n - self._max_visible)
        self._scroll_offset = max(0, min(max_offset, self._scroll_offset))

    def _filter(self) -> list[tuple[T, int]]:
        """Return filtered+sorted (item, score) pairs using precomputed labels."""
        if not self._query:
            return [(item, 0) for item, _ in self._labeled]
        q = self._query.lower()
        scored: list[tuple[int, int, int, str, T]] = []
        for item, label in self._labeled:
            text = label.lower()
            idx = text.find(q)
            if idx < 0:
                continue
            starts_with = 1 if idx == 0 else 0
            scored.append((-starts_with, idx, len(text), text, item))
        scored.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
        return [(item, -row[0]) for row in scored for item in [row[4]]]

    def visible_items(self) -> list[tuple[T, int]]:
        return self._filter()

    async def commit(self) -> T | None:
        items = list(self.visible_items())
        if not items:
            return None
        chosen, _ = items[self._cursor]
        await self._on_select(chosen)
        return chosen

    # OverlayScreen interface ---------------------------------------------

    def render(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        items = self._filter()
        total = len(items)
        if self._title:
            count = f"{total} match" if total == 1 else f"{total} matches"
            out.append(("class:picker.title", f" {self._title.upper()} "))
            out.append(("class:picker.meta", f" {count}  ↑/↓ move · Enter select · Esc close\n"))
        query = self._query or "type to filter"
        query_style = "class:picker.query" if self._query else "class:picker.query.placeholder"
        out.append(("class:picker.prompt", " search "))
        out.append((query_style, f"{query}\n"))
        out.append(("class:picker.rule", "─" * 72 + "\n"))
        start = self._scroll_offset
        end = min(total, start + self._max_visible)
        for idx in range(start, end):
            item, _ = items[idx]
            # Look up the precomputed label to avoid calling label_fn again.
            label = next((lbl for it, lbl in self._labeled if it is item), str(item))
            if idx == self._cursor:
                out.append(("class:picker.selected", f" ▸ {label} \n"))
            else:
                out.append(("class:picker.row", f"   {label}\n"))
        if not items:
            out.append(("class:hint", "   no matches — try a provider name or auth type\n"))
        if total > self._max_visible:
            hidden_above = start
            hidden_below = total - end
            tag = ""
            if hidden_above and hidden_below:
                tag = f"  ↑ {hidden_above} more · ↓ {hidden_below} more"
            elif hidden_above:
                tag = f"  ↑ {hidden_above} more above"
            elif hidden_below:
                tag = f"  ↓ {hidden_below} more below"
            out.append(("class:picker.footer", tag + "\n"))
        return out

    def handle_key(self, key: str) -> bool:
        # Concrete key handling is wired in the App; this returns False so the
        # App's KeyBindings can route directly. Kept here as the documented
        # extension point.
        return False
