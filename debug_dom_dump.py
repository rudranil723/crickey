"""
debug_dom_dump.py
-----------------
Extracts all unique class names and key text content from a CREX match page.
Use this to find the right CSS selectors for DOM scraping.

Usage:
    python debug_dom_dump.py <slug> <tab>

Examples:
    python debug_dom_dump.py nep-vs-oma-...11HD match-details
    python debug_dom_dump.py nep-vs-oma-...11HD match-squads
"""

import asyncio
import json
import re
import sys

import config
from scraper.browser import pool
from playwright.async_api import TimeoutError as PlaywrightTimeout


async def main():
    slug     = sys.argv[1] if len(sys.argv) > 1 else ""
    tab_path = sys.argv[2] if len(sys.argv) > 2 else "match-details"
    url      = f"{config.MATCH_DETAIL_BASE}/{slug}/{tab_path}".rstrip("/")
    out_file = f"debug_classes_{tab_path.replace('-', '_')}.json"

    print(f"Navigating to: {url}")
    await pool.start()
    try:
        async with pool.page() as page:
            try:
                await page.goto(url, wait_until="networkidle",
                                timeout=config.PAGE_TIMEOUT_MS)
            except PlaywrightTimeout:
                print("[WARN] Timeout")
            await asyncio.sleep(3)

            # 1. All unique class names on the page
            classes = await page.evaluate("""
                () => {
                    const cls = new Set();
                    document.querySelectorAll('[class]').forEach(el => {
                        el.className.toString().split(/\\s+/).forEach(c => {
                            if (c.trim()) cls.add(c.trim());
                        });
                    });
                    return [...cls].sort();
                }
            """)

            # 2. All text content of elements that look like labels/values
            #    (short text, not scripts)
            labels = await page.evaluate("""
                () => {
                    const results = [];
                    const els = document.querySelectorAll(
                        'td, th, li, span, p, h1, h2, h3, h4, label, [class*="info"], [class*="detail"], [class*="player"], [class*="squad"], [class*="team"]'
                    );
                    els.forEach(el => {
                        const t = el.innerText ? el.innerText.trim() : '';
                        if (t.length > 0 && t.length < 200 && !t.includes('\\n\\n')) {
                            results.push({
                                tag: el.tagName,
                                cls: el.className.toString().substring(0, 80),
                                text: t.substring(0, 120)
                            });
                        }
                    });
                    return results.slice(0, 200);
                }
            """)

            result = {"classes": classes, "elements": labels}
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            print(f"\nFound {len(classes)} unique classes. Saving to {out_file}")
            print("\n--- Classes containing 'info', 'detail', 'squad', 'player', 'team', 'venue', 'toss', 'umpire' ---")
            keywords = ['info', 'detail', 'squad', 'player', 'team', 'venue',
                        'toss', 'umpire', 'match', 'series', 'ground']
            for c in classes:
                if any(kw in c.lower() for kw in keywords):
                    print(f"  .{c}")

            print("\n--- First 60 text elements ---")
            for el in labels[:60]:
                print(f"  <{el['tag']} class='{el['cls']}'> {el['text']!r}")

    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
