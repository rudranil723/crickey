"""
scraper/browser.py
------------------
Manages a single shared Playwright browser instance with a pool of
reusable BrowserContext objects.

Design decisions
----------------
* One Playwright browser process is launched for the lifetime of the app.
* Each "slot" in the pool is a fresh BrowserContext (separate cookies /
  cache) so concurrent scrapes don't interfere.
* asyncio.Semaphore caps simultaneous pages to MAX_CONCURRENT_PAGES.
* Contexts are recycled after each use to avoid memory creep.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

import config
from utils.logger import log


class BrowserPool:
    """
    Singleton-like async browser pool.

    Usage:
        pool = BrowserPool()
        await pool.start()

        async with pool.page() as page:
            await page.goto(url)

        await pool.stop()
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(
            config.MAX_CONCURRENT_PAGES
        )

    async def start(self) -> None:
        log.info("Launching Playwright browser (headless={})", config.HEADLESS)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        log.info("Browser ready")

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log.info("Browser shut down")

    @asynccontextmanager
    async def page(self) -> AsyncGenerator[Page, None]:
        """
        Acquire a semaphore slot, create a fresh BrowserContext + Page,
        yield the page, then clean up — regardless of success or failure.
        """
        async with self._semaphore:
            if self._browser is None:
                raise RuntimeError("BrowserPool not started — call await pool.start()")

            context: BrowserContext = await self._browser.new_context(
                viewport=config.VIEWPORT,
                user_agent=config.USER_AGENT,
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            # Mask automation signals
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            pg: Page = await context.new_page()
            pg.set_default_timeout(config.PAGE_TIMEOUT_MS)

            try:
                yield pg
            finally:
                try:
                    await pg.close()
                except Exception:
                    pass
                try:
                    await context.close()
                except Exception:
                    pass


# Module-level singleton shared across the whole app
pool = BrowserPool()
