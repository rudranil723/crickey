"""
debug_dom.py
------------
Saves the fully-rendered HTML of a match page tab so we can inspect
the real CSS class names CREX uses (squads, match info, etc.)

Usage:
    python debug_dom.py <slug> <tab-path>

Examples:
    python debug_dom.py nep-vs-oma-...11HD match-squads
    python debug_dom.py nep-vs-oma-...11HD match-details
"""

import asyncio
import sys

import config
from scraper.browser import pool
from playwright.async_api import TimeoutError as PlaywrightTimeout


async def main():
    slug     = sys.argv[1] if len(sys.argv) > 1 else ""
    tab_path = sys.argv[2] if len(sys.argv) > 2 else "match-squads"
    url      = f"{config.MATCH_DETAIL_BASE}/{slug}/{tab_path}".rstrip("/")
    out_file = f"debug_{tab_path.replace('/', '_')}.html"

    print(f"Navigating to: {url}")
    await pool.start()
    try:
        async with pool.page() as page:
            try:
                await page.goto(url, wait_until="networkidle",
                                timeout=config.PAGE_TIMEOUT_MS)
            except PlaywrightTimeout:
                print("[WARN] Timeout — saving partial DOM")
            await asyncio.sleep(3)  # let JS finish rendering
            html = await page.content()
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Saved {len(html):,} bytes to {out_file}")
            print("Open this file in a browser or text editor to inspect class names.")
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
