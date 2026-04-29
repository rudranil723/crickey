"""
debug_intercept.py
------------------
Dumps every API response intercepted during a match page load.
Run this to discover which endpoint carries match info, venue, squads etc.

Usage:
    python debug_intercept.py <slug> [tab]

Examples:
    python debug_intercept.py nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD
    python debug_intercept.py nep-vs-oma-... match-details
    python debug_intercept.py nep-vs-oma-... match-squads
"""

import asyncio
import json
import sys

import config
from scraper.browser import pool
from scraper.interceptor import APIInterceptor
from playwright.async_api import TimeoutError as PlaywrightTimeout


TABS = {
    "scorecard":     "match-scorecard",
    "match-details": "match-details",
    "squads":        "match-squads",
    "live":          "",
    "":              "",
}


async def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
    tab  = sys.argv[2] if len(sys.argv) > 2 else "match-details"
    tab_path = TABS.get(tab, tab)
    url  = f"{config.MATCH_DETAIL_BASE}/{slug}/{tab_path}".rstrip("/")

    print(f"\n{'='*70}")
    print(f"Navigating to: {url}")
    print(f"{'='*70}\n")

    await pool.start()
    try:
        async with pool.page() as page:
            interceptor = APIInterceptor(page)
            await interceptor.attach()
            try:
                await page.goto(url, wait_until="networkidle", timeout=config.PAGE_TIMEOUT_MS)
            except PlaywrightTimeout:
                print("[WARN] Page timeout — dumping what was captured so far\n")

            await asyncio.sleep(3)

            print(f"Captured {len(interceptor.captured)} API responses:\n")
            for i, item in enumerate(interceptor.captured, 1):
                short = item['url']
                data  = item['data']

                # Top-level keys
                if isinstance(data, dict):
                    keys = list(data.keys())[:20]
                    preview = json.dumps(
                        {k: data[k] for k in keys[:5]},
                        ensure_ascii=False, default=str
                    )[:300]
                elif isinstance(data, list):
                    keys = ["<list>", f"len={len(data)}"]
                    preview = json.dumps(data[:2], ensure_ascii=False, default=str)[:300]
                else:
                    keys = [type(data).__name__]
                    preview = str(data)[:200]

                print(f"[{i}] {short}")
                print(f"     Keys: {keys}")
                print(f"     Preview: {preview}")
                print()

            # Also save full dump to file for inspection
            out = [{"url": x["url"], "data": x["data"]} for x in interceptor.captured]
            fname = f"debug_api_dump_{tab or 'live'}.json"
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2, default=str)
            print(f"Full dump saved to: {fname}")
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
