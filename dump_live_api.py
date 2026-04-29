import asyncio, json
from scraper.browser import pool
from scraper.interceptor import APIInterceptor

SLUG = 'nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD'

async def dump_pages_and_api():
    await pool.start()
    
    # 1. Dump Live HTML
    async with pool.page() as page:
        url = f'https://crex.com/cricket-live-score/{SLUG}'
        print(f'Navigating to Live: {url}')
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(4)
        html = await page.content()
        with open('debug_live.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print('live HTML saved:', len(html), 'bytes')

    # 2. Capture API for Scorecard
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        url = f'https://crex.com/cricket-live-score/{SLUG}/match-scorecard'
        print(f'Navigating to Scorecard (API Capture): {url}')
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(4)
        print(f'Captured {len(interceptor.captured)} API responses:')
        for cap in interceptor.captured:
            url_captured = cap.get('url', '')
            data = cap.get('data', {})
            if isinstance(data, dict):
                keys = list(data.keys())
            elif isinstance(data, list):
                keys = f'list[{len(data)}]'
            else:
                keys = type(data).__name__
            print(f'  {url_captured[:80]}')
            print(f'    keys: {keys}')
            
            # If it looks like scorecard data, dump a bit more
            if 'getSC' in url_captured or 'scorecard' in url_captured.lower():
                 print(f'    Scorecard Data Snippet: {str(data)[:200]}...')

    await pool.stop()

if __name__ == "__main__":
    asyncio.run(dump_pages_and_api())
