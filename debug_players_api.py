"""
Check ALL API calls across all 4 tab pages to find player name lookup
"""
import asyncio, json
from playwright.async_api import async_playwright

SLUG = "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
BASE = "https://crex.com/cricket-live-score"

PAGES = [
    ("squads",      f"{BASE}/{SLUG}/match-details"),
    ("scorecard",   f"{BASE}/{SLUG}/match-scorecard"),
    ("live",        f"{BASE}/{SLUG}"),
]

async def main():
    all_found = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        for label, url in PAGES:
            page = await context.new_page()
            found = {}

            async def handle(response, found=found):
                try:
                    u = response.url
                    if "goscorer.com" in u or "crickapi.com" in u:
                        data = await response.json()
                        endpoint = u.split("?")[0].split("/")[-1]
                        found[endpoint] = {"url": u, "data": data}
                except:
                    pass

            page.on("response", handle)
            print(f"\n[{label}] Loading {url}")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)

            print(f"  Endpoints fired: {list(found.keys())}")
            for ep, info in found.items():
                data = info["data"]
                if isinstance(data, dict):
                    # Look for any key containing player names (long strings)
                    for k, v in data.items():
                        if isinstance(v, str) and len(v) > 50:
                            print(f"    {k}: {v[:120]}")
                        elif isinstance(v, list) and v and isinstance(v[0], dict):
                            if any(key in v[0] for key in ['name','playerName','fullName','pn']):
                                print(f"    >>> PLAYER LIST in {k}! first={v[0]}")

            all_found[label] = found
            await page.close()

        await browser.close()

    # Save full dump
    # Remove non-serializable
    with open("debug_all_tabs.json", "w", encoding="utf-8") as f:
        json.dump({
            label: {ep: info["data"] for ep, info in tabs.items()}
            for label, tabs in all_found.items()
        }, f, indent=2, ensure_ascii=False)
    print("\nSaved debug_all_tabs.json")

asyncio.run(main())