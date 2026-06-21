"""Per-profile Playwright-client pool.

When our overridden `browser_click` / `browser_type` / etc. fire, they need
an async Playwright client attached to the manager-provisioned CDP URL of
the active profile. We DON'T want to reconnect for every tool call —
that:
  - costs 100-300ms per click (CDP handshake),
  - resets the in-process cursor position (cloakbrowser tracks cursor on
    the patched Browser object), so the next click would teleport from
    (0,0) instead of where the previous click left off.

This module keeps a dict ``{cdp_url: PooledClient}`` shared across the
worker process. ``PooledClient.get(cdp_url)`` is the only entry point —
it lazy-connects on first use, patches the browser via
``cloakbrowser.human.patch_browser_async`` (which now uses our pydoll math
because the sys.modules hook ran at plugin import time), and caches.

Concurrency: one asyncio Lock per ``cdp_url`` key, so simultaneous tool
calls against the same profile serialize through the same patched
Browser/Page (which is what cloakbrowser's wrappers expect anyway —
they assume single-threaded access per Browser).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PooledClient:
    """A connected, patched Playwright client for one profile."""
    playwright: Any
    browser: Any
    context: Any
    page: Any
    cdp_url: str


class BrowserPool:
    """One pool per process. Get the singleton via ``get_pool()``."""

    def __init__(self) -> None:
        self._clients: Dict[str, PooledClient] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._pool_lock = asyncio.Lock()
        # The HumanConfig instance we apply on each new connection.
        # Loaded lazily because cloakbrowser-resolve happens after
        # humanize.install().
        self._human_cfg: Any = None
        self._human_preset: Optional[str] = None

    # ------- locking ------- #

    async def _lock_for(self, cdp_url: str) -> asyncio.Lock:
        async with self._pool_lock:
            lock = self._locks.get(cdp_url)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[cdp_url] = lock
            return lock

    # ------- the only public method anyone needs ------- #

    async def get(self, cdp_url: str, preset: str = "default") -> PooledClient:
        """Return a connected, patched client for `cdp_url`. Cache-aware."""
        lock = await self._lock_for(cdp_url)
        async with lock:
            existing = self._clients.get(cdp_url)
            if existing is not None and _is_alive(existing):
                return existing

            client = await self._connect_and_patch(cdp_url, preset)
            self._clients[cdp_url] = client
            return client

    async def drop(self, cdp_url: str) -> None:
        """Close and forget the client for `cdp_url`."""
        lock = await self._lock_for(cdp_url)
        async with lock:
            client = self._clients.pop(cdp_url, None)
            if client is None:
                return
            try:
                await client.browser.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("browser.close() failed on drop: %s", exc)
            try:
                await client.playwright.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("playwright.stop() failed on drop: %s", exc)

    async def drop_all(self) -> None:
        for url in list(self._clients.keys()):
            await self.drop(url)

    # ------- internals ------- #

    async def _connect_and_patch(self, cdp_url: str, preset: str) -> PooledClient:
        from playwright.async_api import async_playwright

        # Imported here so the humanize sys.modules hook (run at plugin
        # __init__ load) gets a chance to install before cloakbrowser is
        # imported anywhere.
        from cloakbrowser.human import patch_browser_async, resolve_config

        logger.info("BrowserPool: connecting to CDP %s (preset=%s)", cdp_url, preset)

        pw = await async_playwright().start()
        connect_kwargs: Dict[str, Any] = {}
        token = os.environ.get("CLOAK_AUTH_TOKEN", "").strip()
        if token:
            connect_kwargs["headers"] = {"Authorization": f"Bearer {token}"}
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_url, **connect_kwargs)
        except Exception:
            await pw.stop()
            raise

        # Manager-provisioned browser always has at least one context (it
        # launched chromium with launch_persistent_context). We grab that.
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        if self._human_cfg is None or self._human_preset != preset:
            self._human_cfg = resolve_config(preset)
            self._human_preset = preset

        # Patch the browser — sync in current cloakbrowser releases.
        patch_browser_async(browser, self._human_cfg)

        return PooledClient(
            playwright=pw,
            browser=browser,
            context=context,
            page=page,
            cdp_url=cdp_url,
        )


_singleton: Optional[BrowserPool] = None


def get_pool() -> BrowserPool:
    global _singleton
    if _singleton is None:
        _singleton = BrowserPool()
    return _singleton


def _is_alive(client: PooledClient) -> bool:
    """Cheap liveness check — Playwright marks browsers as not-connected
    once the underlying transport drops."""
    try:
        if not client.browser.is_connected():
            return False
        if client.page.is_closed():
            return False
        if client.context not in client.browser.contexts:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False
