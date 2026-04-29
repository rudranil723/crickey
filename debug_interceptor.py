import asyncio
import config
from scraper.browser import pool
from scraper.interceptor import APIInterceptor

async def debug():
    await pool.start()
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await page.goto(config.MATCH_LIST_URL, wait_until='networkidle', timeout=60000)
        print("Captured", len(interceptor.captured), "API responses:")
        for c in interceptor.captured:
            print(" ", c["url"])
    await pool.stop()

asyncio.run(debug())