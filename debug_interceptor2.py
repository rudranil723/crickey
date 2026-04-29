"""
debug_interceptor2.py
Logs ALL network responses (no filtering) to find CREX's real API URLs.
"""
import asyncio
from playwright.async_api import async_playwright

URL = "https://crex.com/fixtures/match-list"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        all_responses = []

        # Capture EVERY response URL — no filtering at all
        async def on_response(response):
            all_responses.append((response.status, response.url))

        page.on("response", on_response)

        print("Navigating to CREX match list...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        # Wait 8 seconds for Angular to boot and fire all API calls
        print("Waiting 8s for Angular API calls to fire...")
        await asyncio.sleep(8)

        await browser.close()

    print(f"\nTotal responses captured: {len(all_responses)}")
    print("\n--- All URLs ---")
    for status, url in all_responses:
        print(f"  [{status}] {url}")

asyncio.run(main())