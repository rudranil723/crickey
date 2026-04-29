import asyncio
import argparse
from scraper.browser import pool
from scraper.interceptor import APIInterceptor
from utils.logger import setup_logging
import config

async def debug_intercept(url):
    await pool.start()
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(5)
        print("\nCaptured URLs:")
        for item in interceptor.captured:
            print(f"  {item['url']}")
    await pool.stop()

if __name__ == "__main__":
    setup_logging()
    url = "https://crex.com/cricket-live-score/ban-vs-nz-2nd-t20-new-zealand-tour-of-bangladesh-2026-match-updates-10Z4/match-scorecard"
    asyncio.run(debug_intercept(url))
