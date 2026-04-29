"""
Dumps full getSC4 scorecard API data + detailed DOM structure.
Run: python inspect_scorecard.py
"""
import asyncio, json
from scraper.browser import pool
from scraper.interceptor import APIInterceptor

SLUG = 'nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD'

async def inspect():
    await pool.start()
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        url = f'https://crex.com/cricket-live-score/{SLUG}/match-scorecard'
        await page.goto(url, wait_until='networkidle', timeout=30000)
        
        # Wait longer for scorecard tables
        try:
            await page.wait_for_selector("[class*='team-inning']", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # 1. Dump full API response
        for cap in interceptor.captured:
            u = cap.get('url', '')
            if 'getSC' in u:
                print('=== getSC4 full response ===')
                print(json.dumps(cap['data'], default=str, indent=2)[:2000])

        # 2. Inspect DOM structure of first team-inning section
        print('\n=== DOM: team-inning sections ===')
        sections = await page.query_selector_all("[class*='team-inning']")
        print(f'Found {len(sections)} team-inning sections')

        if sections:
            sec = sections[0]
            team_el = await sec.query_selector("[class*='team-name']")
            if team_el:
                print('Team name:', await team_el.inner_text())

            # All rows with player data
            player_rows = await sec.query_selector_all("[class*='player-data']")
            print(f'player-data rows: {len(player_rows)}')
            for i, row in enumerate(player_rows[:3]):
                txt = (await row.inner_text()).strip()
                print(f'  row {i}: {repr(txt[:100])}')
                # Look at child elements
                children = await row.query_selector_all("[class]")
                for child in children[:5]:
                    cls = await child.get_attribute('class')
                    txt2 = (await child.inner_text()).strip()
                    print(f'    child class={cls!r}: {repr(txt2[:50])}')

            # Check bowler-table
            bowler_tables = await sec.query_selector_all("[class*='bowler-table']")
            print(f'\nbowler-table sections: {len(bowler_tables)}')
            for tbl in bowler_tables[:1]:
                rows = await tbl.query_selector_all('tr')
                print(f'  rows in bowler-table: {len(rows)}')
                for i, row in enumerate(rows[:3]):
                    txt = (await row.inner_text()).strip()
                    print(f'  row {i}: {repr(txt[:100])}')
                    cells = await row.query_selector_all('td')
                    for j, cell in enumerate(cells[:6]):
                        cls = await cell.get_attribute('class')
                        val = (await cell.inner_text()).strip()
                        print(f'    td[{j}] class={cls!r}: {repr(val)}')

    await pool.stop()

if __name__ == '__main__':
    asyncio.run(inspect())
