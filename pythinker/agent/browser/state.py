"""BrowserContextState and the SSRF route handler bound at context creation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger

from pythinker.security.network import validate_resolved_url

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Route


def storage_path_for_key(storage_dir: Path, effective_key: str) -> Path:
    """Hash-based filename so chat IDs with ':' or '/' don't leak into the FS."""
    digest = hashlib.sha256(effective_key.encode("utf-8")).hexdigest()[:16]
    return storage_dir / f"{digest}.json"


@dataclass
class BrowserContextState:
    """Per-session browser state. Owned by BrowserSessionManager."""

    effective_key: str
    context: "BrowserContext"
    page: "Page"
    storage_path: Path
    lock: asyncio.Lock
    last_used_at: float
    default_timeout_ms: int = 15_000
    navigation_timeout_ms: int = 30_000
    eval_timeout_ms: int = 5_000
    snapshot_max_chars: int = 20_000
    max_pages: int = 5
    last_url: str = "about:blank"
    blocked_this_action: int = 0
    notify_restart_prefix: str | None = None  # set after a CDP reconnect
    dead: bool = field(default=False, init=False)  # set by manager._on_disconnected
    closed: bool = field(default=False, init=False)

    async def save_storage_state(self) -> None:
        """Persist cookies + localStorage to ``storage_path``. Tolerant of dead contexts."""
        try:
            data = await self.context.storage_state()
        except Exception as e:  # context already closed, transport down, etc.
            logger.warning(
                "browser: storage_state save skipped for {} ({})", self.effective_key, e
            )
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.storage_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.storage_path)

    async def close(self) -> None:
        """Save state, close the BrowserContext. Idempotent."""
        if self.closed:
            return
        await self.save_storage_state()
        try:
            await self.context.close()
        except Exception as e:
            logger.debug("browser: context close error for {} ({})", self.effective_key, e)
        self.closed = True

    async def enforce_page_limit(self) -> int:
        """Close extra pages beyond ``max_pages`` and return the number closed."""
        pages = getattr(self.context, "pages", None)
        if callable(pages):
            pages = pages()
        if not isinstance(pages, list) or len(pages) <= self.max_pages:
            return 0
        keep: list[object] = []
        if self.page in pages:
            keep.append(self.page)
        for page in pages:
            if page is self.page:
                continue
            if len(keep) >= self.max_pages:
                break
            keep.append(page)
        extras = [page for page in pages if page not in keep]
        closed = 0
        for page in extras:
            try:
                close = getattr(page, "close", None)
                if callable(close):
                    await close()
                    closed += 1
            except Exception as e:
                logger.debug("browser: extra page close error for {} ({})", self.effective_key, e)
        return closed


def _ssrf_route_handler(
    state: "BrowserContextState",
) -> Callable[["Route"], Awaitable[None]]:
    """Build a Playwright route handler that aborts SSRF-disallowed requests.

    Closes over ``state`` so it can increment ``state.blocked_this_action``
    directly. Caller resets that counter to 0 at the start of each action so
    the per-action sub-request block count surfaces cleanly in the result string.
    """

    async def handler(route: "Route") -> None:
        url = route.request.url
        ok, err = validate_resolved_url(url)
        if not ok:
            state.blocked_this_action += 1
            logger.warning("browser: blocked sub-request {} ({})", url, err)
            await route.abort(error_code="blockedbyclient")
            return
        await route.continue_()

    return handler
