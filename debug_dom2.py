"""
debug_dom2.py
Prints raw HTML of ALL match cards so we can find
start_time and series_name selectors in upcoming matches.
"""
import asyncio
from playwright.async_api import async_playwright

URL = "https://crex.com/schedule"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)

        cards = await page.query_selector_all(".match-card-wrapper")
        print(f"Total cards: {len(cards)}")

        # Print cards 2, 3, 4 (index 1,2,3) — these should be upcoming
        for i in [1, 2, 3]:
            if i < len(cards):
                html = await cards[i].inner_html()
                print(f"\n=== CARD {i+1} HTML ===")
                print(html[:3000])

        await browser.close()

asyncio.run(main())