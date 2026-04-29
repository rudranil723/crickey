"""
debug_api_dump.py
Dumps the raw JSON from getSV3 and getSC4 so we can see the real structure.
"""
import asyncio, json
from playwright.async_api import async_playwright

SLUG = "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
BASE = "https://crex.com/cricket-live-score"

async def dump(url, label):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        captured = {}

        async def on_response(response):
            if "goscorer.com" in response.url or "crickapi.com" in response.url:
                try:
                    data = await response.json()
                    captured[response.url] = data
                except:
                    pass

        page.on("response", on_response)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)
        await browser.close()

        print(f"\n{'='*60}")
        print(f"{label}: {url}")
        print(f"Captured {len(captured)} API responses")
        for api_url, data in captured.items():
            print(f"\n  URL: {api_url}")
            print(f"  Type: {type(data).__name__}")
            if isinstance(data, dict):
                print(f"  Top keys: {list(data.keys())}")
                # Print first 2 levels
                for k, v in list(data.items())[:8]:
                    if isinstance(v, (dict, list)):
                        sub = list(v.keys())[:6] if isinstance(v, dict) else f"[list of {len(v)}]"
                        print(f"    {k}: {sub}")
                    else:
                        print(f"    {k}: {str(v)[:100]}")
            elif isinstance(data, list):
                print(f"  List length: {len(data)}")
                if data and isinstance(data[0], dict):
                    print(f"  First item keys: {list(data[0].keys())[:10]}")

async def main():
    await dump(f"{BASE}/{SLUG}/match-details", "MATCH INFO + SQUADS")
    await dump(f"{BASE}/{SLUG}/match-scorecard", "SCORECARD")
    await dump(f"{BASE}/{SLUG}", "LIVE")

asyncio.run(main())