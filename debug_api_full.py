"""
debug_api_full.py
Dumps FULL raw JSON for getSV3, getSC4, getBallFeeds
"""
import asyncio, json
from playwright.async_api import async_playwright

SLUG = "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
BASE = "https://crex.com/cricket-live-score"

TARGETS = ["getSV3", "getSC4", "getBallFeeds"]

async def dump_all():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        captured = {}

        async def on_response(response):
            if any(t in response.url for t in TARGETS):
                try:
                    data = await response.json()
                    captured[response.url] = data
                except:
                    pass

        page.on("response", on_response)

        # Hit scorecard page — triggers both getSV3 and getSC4
        await page.goto(f"{BASE}/{SLUG}/match-scorecard",
                        wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        # Now hit live page — triggers getBallFeeds
        await page.goto(f"{BASE}/{SLUG}",
                        wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        await browser.close()

    for url, data in captured.items():
        label = next((t for t in TARGETS if t in url), url)
        with open(f"debug_{label}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved debug_{label}.json  ({len(str(data))} chars)")

asyncio.run(dump_all())