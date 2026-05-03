"""BrowserSessionManager: one browser process/connection, one context per effective_key."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from pythinker.agent.browser.state import (
    BrowserContextState,
    _ssrf_route_handler,
    storage_path_for_key,
)
from pythinker.agent.browser.transport import cdp_healthcheck

if TYPE_CHECKING:
    from playwright.async_api import Playwright

    from pythinker.config.schema import BrowserConfig


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
_PROVISION_COMMAND = (sys.executable, "-m", "playwright", "install", "chromium")
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SAFE_LAUNCH_ARGS = ("--disable-gpu", "--disable-dev-shm-usage")


class _MissingChromiumError(Exception):
    """Internal signal: launch failed because the Chromium binary is absent.

    Raised inside ``_connect_lock`` so the caller can drop the lock before
    awaiting ``_provision_chromium`` — a 30-300 s subprocess that should not
    block other chats from acquiring browser contexts.
    """


async def _default_pw_factory() -> "Playwright":
    """Default Playwright entry point. Imported lazily for fast non-browser startup."""
    from playwright.async_api import async_playwright

    return await async_playwright().start()


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _is_missing_browser_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "executable doesn't exist" in text
        or "executable does not exist" in text
        or "playwright install" in text
        or "looks like playwright was just installed" in text
    )


def _is_sandbox_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "sandbox" in text and (
        "no usable sandbox" in text
        or "namespace" in text
        or "zygote" in text
        or "setuid" in text
        or "seccomp" in text
    )


def _provision_command_text() -> str:
    return "python -m playwright install chromium"


def _compact_process_output(stdout: bytes, stderr: bytes) -> str:
    output = "\n".join(
        part.decode("utf-8", errors="replace").strip()
        for part in (stdout, stderr)
        if part
    ).strip()
    if len(output) > 2000:
        return output[:2000] + "\n[…truncated…]"
    return output


class BrowserSessionManager:
    """Manage a browser and isolated BrowserContextState objects.

    Production uses either Playwright-managed launch mode or CDP mode. Tests
    inject ``_pw_factory`` so no real browser is needed.
    """

    def __init__(
        self,
        config: "BrowserConfig",
        storage_dir: Path,
        *,
        _pw_factory: Callable[[], Awaitable["Playwright"]] = _default_pw_factory,
    ) -> None:
        self._config = config
        self._storage_dir = storage_dir
        self._pw_factory = _pw_factory
        self._pw: Any = None  # Playwright | None
        self._browser: Any = None  # Browser | None
        self._browser_mode: str | None = None
        self._states: dict[str, BrowserContextState] = {}
        self._connect_lock = asyncio.Lock()
        self._provision_lock = asyncio.Lock()
        self._notice_once: str | None = None

    def _has_configured_cdp(self) -> bool:
        return self._config.cdp_url.rstrip("/") != DEFAULT_CDP_URL

    async def _ensure_playwright(self) -> Any:
        if self._pw is None:
            self._pw = await self._pw_factory()
        return self._pw

    def _browser_is_connected(self) -> bool:
        if self._browser is None:
            return False
        is_connected = getattr(self._browser, "is_connected", None)
        if callable(is_connected):
            return bool(is_connected())
        return True

    async def _ensure_browser(self) -> Any:
        # Two passes at most: first attempt under _connect_lock; on missing
        # Chromium, drop the lock, await provisioning (under _provision_lock
        # only), then retry. Releasing _connect_lock during provisioning is
        # what keeps a 30-300 s `playwright install` from blocking every
        # other chat that wants its own browser context.
        last_missing: BaseException | None = None
        for attempt in (0, 1):
            async with self._connect_lock:
                if self._browser_is_connected():
                    return self._browser

                mode = self._config.mode
                if mode == "cdp":
                    return await self._connect_cdp()

                if mode == "auto" and self._has_configured_cdp():
                    try:
                        return await self._connect_cdp()
                    except RuntimeError as exc:
                        logger.warning(
                            "browser: configured CDP endpoint unavailable in auto mode; "
                            "falling back to launch mode ({})",
                            exc,
                        )

                try:
                    return await self._launch_browser()
                except _MissingChromiumError as missing:
                    if not self._config.auto_provision:
                        raise RuntimeError(
                            "Playwright Chromium is not installed. Run: "
                            f"{_provision_command_text()}"
                        ) from missing.__cause__ or missing
                    if attempt == 1:
                        # Already provisioned once and still missing — surface
                        # the underlying error rather than loop forever.
                        raise RuntimeError(
                            "Playwright Chromium was provisioned but launch still "
                            f"failed: {missing.__cause__}"
                        ) from missing.__cause__ or missing
                    last_missing = missing
            # _connect_lock released; provision outside the critical section.
            assert last_missing is not None  # for type checkers
            await self._provision_chromium()
            self._notice_once = (
                "[browser: provisioned Playwright Chromium before this action]"
            )
        # Loop exits through return or raise; this is unreachable.
        raise RuntimeError("browser: _ensure_browser loop exhausted unexpectedly")

    async def _connect_cdp(self) -> Any:
        ok, err = await cdp_healthcheck(self._config.cdp_url)
        if not ok:
            raise RuntimeError(
                f"browser CDP endpoint unreachable at {self._config.cdp_url}: {err}"
            )
        pw = await self._ensure_playwright()
        try:
            browser = await pw.chromium.connect_over_cdp(self._config.cdp_url)
        except Exception as exc:
            raise RuntimeError(
                f"browser CDP connection failed at {self._config.cdp_url}: {exc}"
            ) from exc
        self._browser = browser
        self._browser_mode = "cdp"
        self._bind_disconnect_handler(browser)
        logger.info("browser: connected via CDP at {}", self._config.cdp_url)
        return browser

    async def _launch_browser(self) -> Any:
        """Launch managed Chromium; signal _MissingChromiumError if the binary is absent.

        Caller (``_ensure_browser``) catches ``_MissingChromiumError``, drops
        ``_connect_lock``, and awaits ``_provision_chromium`` outside the
        critical section before retrying.
        """
        try:
            browser = await self._launch_once()
        except Exception as exc:
            if _is_missing_browser_error(exc):
                raise _MissingChromiumError() from exc
            if _is_sandbox_error(exc):
                raise RuntimeError(
                    "browser launch failed because Chromium's sandbox could not start. "
                    "Use tools.web.browser.mode='cdp' with the pythinker-browser service "
                    "for hardened container deployments, or set "
                    "PYTHINKER_BROWSER_NO_SANDBOX=1 only as an explicit local escape hatch."
                ) from exc
            raise
        self._browser = browser
        self._browser_mode = "launch"
        self._bind_disconnect_handler(browser)
        logger.info("browser: launched Playwright-managed Chromium")
        return browser

    async def _launch_once(self) -> Any:
        pw = await self._ensure_playwright()
        args = list(_SAFE_LAUNCH_ARGS)
        if _env_flag("PYTHINKER_BROWSER_NO_SANDBOX"):
            logger.warning("browser: launching Chromium with --no-sandbox escape hatch")
            args.append("--no-sandbox")
        headless = False if _env_flag("PYTHINKER_BROWSER_HEADFUL") else self._config.headless
        kwargs: dict[str, Any] = {"headless": headless, "args": args}
        if self._config.executable_path:
            kwargs["executable_path"] = self._config.executable_path
        return await pw.chromium.launch(**kwargs)

    async def _provision_chromium(self) -> None:
        async with self._provision_lock:
            timeout_s = self._config.provision_timeout_s
            logger.info("browser: provisioning chromium (timeout={}s)", timeout_s)
            proc = await asyncio.create_subprocess_exec(
                *_PROVISION_COMMAND,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise RuntimeError(
                    "Timed out provisioning Playwright Chromium. Retry manually with: "
                    f"{_provision_command_text()}"
                ) from exc
            if proc.returncode != 0:
                detail = _compact_process_output(stdout, stderr)
                raise RuntimeError(
                    "Failed to provision Playwright Chromium"
                    f" (exit {proc.returncode}). Run manually: {_provision_command_text()}"
                    + (f"\n{detail}" if detail else "")
                )
            logger.info("browser: chromium provisioning complete")

    def _bind_disconnect_handler(self, browser: Any) -> None:
        on = getattr(browser, "on", None)
        if callable(on):
            on("disconnected", self._on_disconnected)

    def _on_disconnected(self, _browser: Any = None) -> None:
        logger.warning("browser: disconnected; states will be recreated on next acquire")
        for state in self._states.values():
            state.dead = True
            state.notify_restart_prefix = (
                f"[browser session was restarted; previous url was {state.last_url}]"
            )
        self._browser = None
        self._browser_mode = None

    async def acquire(self, effective_key: str) -> BrowserContextState:
        """Get the state for ``effective_key``, creating it lazily."""
        existing = self._states.get(effective_key)
        if (
            existing is not None
            and not existing.dead
            and not existing.closed
            and existing.context is not None
        ):
            existing.last_used_at = time.monotonic()
            return existing

        carried_prefix = existing.notify_restart_prefix if existing is not None else None
        if existing is not None:
            self._states.pop(effective_key, None)

        browser = await self._ensure_browser()
        if self._notice_once:
            carried_prefix = (
                f"{carried_prefix}\n{self._notice_once}"
                if carried_prefix else self._notice_once
            )
            self._notice_once = None
        storage_path = storage_path_for_key(self._storage_dir, effective_key)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "user_agent": _USER_AGENT,
            "locale": "en-US",
            "viewport": {"width": 1280, "height": 800},
        }
        if storage_path.exists():
            kwargs["storage_state"] = str(storage_path)
        context = await browser.new_context(**kwargs)
        context.set_default_timeout(self._config.default_timeout_ms)
        page = await context.new_page()
        state = BrowserContextState(
            effective_key=effective_key,
            context=context,
            page=page,
            storage_path=storage_path,
            lock=asyncio.Lock(),
            last_used_at=time.monotonic(),
            default_timeout_ms=self._config.default_timeout_ms,
            navigation_timeout_ms=self._config.navigation_timeout_ms,
            eval_timeout_ms=self._config.eval_timeout_ms,
            snapshot_max_chars=self._config.snapshot_max_chars,
            max_pages=self._config.max_pages_per_context,
            notify_restart_prefix=carried_prefix,
        )
        await context.route("**/*", _ssrf_route_handler(state))
        self._states[effective_key] = state
        logger.info("browser: opened context for {}", effective_key)
        return state

    async def close_session(self, effective_key: str) -> None:
        """Save + close one chat's context. Idempotent."""
        state = self._states.pop(effective_key, None)
        if state is None:
            return
        await state.close()
        if self._config.disconnect_on_idle and not self._states:
            await self._close_browser()

    async def evict_idle(self) -> int:
        """Close idle contexts and optionally close the managed browser."""
        ttl = self._config.idle_ttl_seconds
        if ttl <= 0:
            return 0
        now = time.monotonic()
        keys = [
            key for key, state in self._states.items()
            if not state.closed
            and not state.dead
            and not state.lock.locked()
            and now - state.last_used_at >= ttl
        ]
        for key in keys:
            await self.close_session(key)
        if self._config.disconnect_on_idle and not self._states:
            await self._close_browser()
        return len(keys)

    async def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                result = self._browser.close()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.debug("browser: close error ({})", e)
            self._browser = None
            self._browser_mode = None

    async def shutdown(self, force: bool = False) -> None:
        """Save + close everything; close browser; stop Playwright.

        ``force=True`` skips per-context save/close and just tears down the
        shared browser + Playwright. Used by hot-reload when a graceful
        shutdown exceeded its deadline — at that point the per-context
        cleanup is what was hanging, so retrying it would hang again.
        """
        if force:
            self._states.clear()
        else:
            keys = list(self._states.keys())
            await asyncio.gather(
                *(self.close_session(k) for k in keys), return_exceptions=True
            )
        await self._close_browser()
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as e:
                logger.debug("browser: pw.stop error during shutdown ({})", e)
            self._pw = None
