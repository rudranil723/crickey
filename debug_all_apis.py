"""
Capture EVERY api.goscorer.com call made during page load
"""
import asyncio, json
from playwright.async_api import async_playwright

SLUG = "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
BASE = "https://crex.com/cricket-live-score"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        all_apis = {}

        async def handle_response(response):
            try:
                url = response.url
                if "goscorer.com" in url or "crickapi.com" in url:
                    data = await response.json()
                    all_apis[url] = data
                    print(f"  ✓ {url.split('?')[0].split('/')[-1]}  keys={list(data.keys()) if isinstance(data, dict) else f'list[{len(data)}]'}")
            except:
                pass

        page.on("response", handle_response)

        # Hit match-details (has player info for squads)
        print("\n--- match-details ---")
        await page.goto(f"{BASE}/{SLUG}/match-details",
                        wait_until="networkidle", timeout=60000)
        await asyncio.sleep(4)

        await browser.close()

    # Save all
    with open("debug_all_apis.json", "w", encoding="utf-8") as f:
        json.dump(all_apis, f, indent=2, ensure_ascii=False)
    print(f"\nSaved debug_all_apis.json  ({len(all_apis)} endpoints)")

asyncio.run(main())