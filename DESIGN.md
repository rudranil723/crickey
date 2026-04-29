# Design Decisions & Architecture

> Answers to the follow-up questions listed in the assignment brief.

---

## Why This Scraping Approach?

CREX is a fully client-side **Angular SPA** — the server returns a near-empty HTML shell
and all data is injected by JavaScript at runtime.  
Two strategies were evaluated:

| Strategy | Verdict |
|---|---|
| Raw HTTP + BeautifulSoup | ❌ Server returns empty HTML — no data |
| Selenium / full browser | ✅ Works but slow; no network inspection |
| **Playwright + API interception** | ✅ **Chosen** — fast, inspects XHR, structured data |

### The Key Insight — Intercepting the Backend API

Opening DevTools on any CREX match page reveals that the browser fetches data from two
endpoints on `api.goscorer.com`:

```
GET api.goscorer.com/api/v3/getSV3   → live match state (minified single-letter keys)
GET api.goscorer.com/api/v3/getSC4   → scorecard innings data
GET content.crickapi.com/commentary/getBallFeeds → ball-by-ball commentary
```

Rather than parsing the rendered DOM (which is fragile and slow), `scraper/interceptor.py`
registers a Playwright `on("response")` handler that captures these JSON payloads directly
as the browser receives them.

**Benefits:**
- Data arrives in structured JSON — no HTML parsing, no CSS selector brittleness
- Minified keys are reverse-engineered once and documented in `match_detail.py` docstring
- Scorecard (getSC4) and live state (getSV3) are available 2–3 seconds after page load,
  before the DOM finishes rendering

**DOM scraping is used only where no API exists:**
- Match venue, city, date, series name, toss, umpires → `.venue-detail`, `.series-name`,
  `.toss-wrap`, `.umpire-val` (confirmed selectors, not guessed)
- Playing XI / Squads → `.playingxi-teams` on the match-details page
  (the `/match-squads` URL renders a blank page — confirmed by inspection)

---

## How Can Resource Usage Be Further Optimised?

### Current Usage
- 1 headless Chromium browser, 1 context per scrape job
- Browser pool (`scraper/browser.py`) reuses a single instance across concurrent tasks
- APScheduler runs the match-list poll every 5 minutes; live/scorecard polls run only
  while a match is `In Progress`

### Next-Level Optimisation: Skip the Browser Entirely

Now that the API endpoints are known, live and scorecard data can be fetched with a
plain HTTP client — **no browser required**:

```python
import httpx

async def fetch_sv3(match_key: str) -> dict:
    # Direct call to the same endpoint Playwright intercepts
    url = f"https://api.goscorer.com/api/v3/getSV3?key={match_key}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers={"Referer": "https://crex.com/"})
        return r.json()["data"]
```

**Impact:**
- Memory: ~200 MB (Chromium) → ~10 MB (httpx async)
- Per-poll latency: ~6 s → ~0.3 s
- CPU: near-zero vs. full JS rendering engine
- Concurrency: hundreds of simultaneous match polls vs. ~5 with Playwright

The browser is still needed for the match list (fixture page is DOM-only) and for
initial match-info / squads scraping. Everything else can migrate to direct HTTP.

### Other Optimisations
- **Incremental polling**: store the last `getSV3["a"]` (overs) value; only re-save
  if overs or score changed — avoids redundant disk writes during drinks breaks
- **Adaptive poll interval**: tighten to 15 s during the last 5 overs of an innings,
  relax to 90 s during rain delays (`fsr == "S"` / `"L"` / `"T"`)
- **Connection pooling**: reuse the same httpx `AsyncClient` across all concurrent
  live polls instead of creating one per match

---

## Can Execution Time Be Reduced Further?

### Current Performance
Full 4-tab scrape of one match: **~33 seconds**  
Match-list poll (20 matches): **~18 seconds** (concurrent DOM fetch)

### Bottlenecks & Solutions

| Bottleneck | Time cost | Fix |
|---|---|---|
| Browser cold start | ~4 s | Keep browser alive across scheduler cycles (already done) |
| `networkidle` wait | ~3–5 s per page | Switch to `domcontentloaded` + wait for specific API response event |
| Sequential tab scrapes | ~30 s for 4 tabs | Run all 4 tabs concurrently (`asyncio.gather`) |
| Match-list DOM parsing | ~18 s | Replace with direct API call once the fixture endpoint is identified |

### Biggest Win: Concurrent Tab Scraping

`scrape_all_static()` already runs Match Info and Squads concurrently.  
Extending this to all four tabs would cut single-match scrape time from ~33 s to ~10 s:

```python
info, squads, scorecard, live = await asyncio.gather(
    scrape_match_info(match_id, slug),
    scrape_squads(match_id, slug),
    scrape_scorecard(match_id, slug),
    scrape_live(match_id, slug),
)
```

(Requires one browser context per coroutine — the pool supports this.)

### Fastest Possible Architecture

Replace Playwright for live/scorecard with direct `httpx` calls (see above) and keep
Playwright only for the fixture list poll. Estimated steady-state poll cycle:

- Fixture list: 18 s every 5 min  
- Per live match (httpx): 0.3 s every 30 s  
- New match info + squads (Playwright, one-shot): ~15 s on discovery

---

## What Would You Change With More Time?

### 1. Direct API Client for Live Data
As described above — eliminate Playwright for `getSV3` / `getSC4` / `getBallFeeds` once
the request headers/cookies needed to bypass anti-bot checks are confirmed.

### 2. Structured Storage (PostgreSQL + TimescaleDB)
Current flat-file JSON storage (`output/<slug>/`) is fine for a demo but has no query
capability. A proper schema:

```
matches      (match_id, slug, series, venue, start_time, status)
innings      (match_id, innings_no, batting_team, total, overs)
batting      (innings_id, player, runs, balls, fours, sixes, strike_rate)
bowling      (innings_id, player, overs, runs, wickets, economy)
live_snaps   (match_id, snapped_at, score, overs, run_rate)  ← TimescaleDB hypertable
```

TimescaleDB's hypertable on `live_snaps` enables sub-second time-series queries:
*"Show me the run rate progression of MI vs SRH over the last 10 overs."*

### 3. Real-Time WebSocket Push (FastAPI)
Replace poll-and-save with a push model:

```
Scheduler polls getSV3 → detects change → publishes to Redis pub/sub
FastAPI WebSocket handler → subscribes → pushes to connected clients
```

Clients (browser dashboard, mobile app) receive score updates in real time without
polling — no wasted HTTP round-trips.

### 4. Player Name Resolution Without getBallFeeds
Currently, player keys in getSC4/getSV3 are resolved to names only if `getBallFeeds`
has fired (i.e., the match has started). A one-time player-key→name registry built
from Squads DOM scraping at match discovery time would make scorecard names correct
even before the first ball.

### 5. Test Coverage
Unit tests for:
- `_decode_score()` edge cases (`"DNB"`, `""`, `"10/0(0.0"`)
- `_parse_sc4_innings()` with real getSC4 payloads
- `_build_key_map()` with over-summary and ball-entry fixtures
- Scheduler trigger logic (mock `utcnow` to simulate match start)

### 6. Docker Deployment
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
COPY . /app
RUN pip install -r /app/requirements.txt
CMD ["python", "/app/main.py"]
```
Single `docker compose up` — no manual Playwright browser install, works on any host.
