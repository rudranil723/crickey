"""
debug_dom3.py - check what status/series selectors actually find per card
"""
import asyncio
from playwright.async_api import async_playwright

URL = "https://crex.com/schedule"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        cards = await page.query_selector_all(".match-card-wrapper")
        print(f"Total cards: {len(cards)}")

        for i, card in enumerate(cards[:6]):
            # team names
            team_els = await card.query_selector_all(".team-name")
            teams = [((await el.inner_text()).strip()) for el in team_els]

            # status
            live_tag = await card.query_selector(".liveTag")
            live_text = (await live_tag.inner_text()).strip() if live_tag else "NONE"

            # not-started block
            ns = await card.query_selector(".not-started")
            ns_exists = "YES" if ns else "NO"

            # start time
            start_el = await card.query_selector(".not-started .start-text")
            start_text = (await start_el.inner_text()).strip() if start_el else "NONE"

            # series
            series_el = await card.query_selector(".not-started .time")
            series_text = (await series_el.inner_text()).strip() if series_el else "NONE"

            print(f"\nCard {i+1}: {teams}")
            print(f"  liveTag='{live_text}' | .not-started={ns_exists}")
            print(f"  start_text='{start_text}' | series='{series_text}'")

        await browser.close()

asyncio.run(main())