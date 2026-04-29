"""
debug_api_full2.py — uses route interception (more reliable than on_response)
"""
import asyncio, json
from playwright.async_api import async_playwright

SLUG = "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
BASE = "https://crex.com/cricket-live-score"
TARGETS = ["getSV3", "getSC4", "getBallFeeds"]

captured = {}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        async def handle_response(response):
            try:
                url = response.url
                if any(t in url for t in TARGETS):
                    data = await response.json()
                    label = next(t for t in TARGETS if t in url)
                    captured[label] = data
                    print(f"  ✓ Captured {label}  ({len(str(data))} chars)")
            except Exception as e:
                pass

        page.on("response", handle_response)

        # ── Scorecard page ──────────────────────────────────────────────
        print("Loading scorecard page...")
        await page.goto(
            f"{BASE}/{SLUG}/match-scorecard",
            wait_until="networkidle",
            timeout=60000
        )
        await asyncio.sleep(3)

        # ── Live page ───────────────────────────────────────────────────
        print("Loading live page...")
        await page.goto(
            f"{BASE}/{SLUG}",
            wait_until="networkidle",
            timeout=60000
        )
        await asyncio.sleep(3)

        await browser.close()

    print(f"\nTotal captured: {list(captured.keys())}")

    for label, data in captured.items():
        fname = f"debug_{label}.json"
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved {fname}")
        # Print preview
        raw = json.dumps(data, indent=2)
        print(f"\n--- {label} preview (first 2000 chars) ---")
        print(raw[:2000])
        if len(raw) > 2000:
            print("...(truncated)")

asyncio.run(main())