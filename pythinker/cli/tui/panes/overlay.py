"""Float-based overlay stack for picker/help/status screens."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class OverlayScreen(ABC):
    @abstractmethod
    def render(self) -> Any:
        """Return prompt_toolkit fragments for this overlay's body."""

    def handle_key(self, key: str) -> bool:
        """Return True iff this overlay consumed the key event."""
        return False


class OverlayContainer:
    def __init__(self) -> None:
        self._stack: list[OverlayScreen] = []

    def push(self, screen: OverlayScreen) -> None:
        self._stack.append(screen)

    def pop(self) -> OverlayScreen | None:
        if self._stack:
            return self._stack.pop()
        return None

    @property
    def visible(self) -> bool:
        return bool(self._stack)

    @property
    def top(self) -> OverlayScreen | None:
        return self._stack[-1] if self._stack else None
