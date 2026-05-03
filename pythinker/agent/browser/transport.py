"""CDP transport helpers for the browser tool: healthcheck and connect."""

from __future__ import annotations

import httpx
from loguru import logger


def _build_version_url(cdp_url: str) -> str:
    """Compose the Chromium DevTools `/json/version` URL from a base CDP URL."""
    return cdp_url.rstrip("/") + "/json/version"


async def cdp_healthcheck(cdp_url: str, timeout_s: float = 2.0) -> tuple[bool, str]:
    """Probe ``{cdp_url}/json/version``. Returns ``(ok, error_message)``.

    Used before opening the websocket so misconfigured / down services fail fast.
    """
    url = _build_version_url(cdp_url)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return True, ""
    except Exception as e:
        logger.debug("browser: CDP healthcheck failed for {} ({})", url, e)
        return False, f"{type(e).__name__}: {e}"
