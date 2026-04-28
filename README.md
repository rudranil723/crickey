# Crickey 🏏

A production-grade **real-time cricket data scraping system** for [CREX](https://crex.com).
Monitors the live fixture list, auto-triggers individual match scrapers when matches start,
and continuously polls **Live** and **Scorecard** data throughout each match.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                      main.py                         │
│            (entry point / CLI / signal handling)     │
└───────────────────┬──────────────────────────────────┘
                    │
        ┌───────────▼──────────────┐
        │      scheduler.py        │
        │  APScheduler AsyncIO     │
        │  ┌──────────────────┐    │
        │  │ Match List Poller│◄───┼── every 5 min
        │  │  (every 5 min)   │    │
        │  └────────┬─────────┘    │
        │           │discovers     │
        │  ┌────────▼─────────┐    │
        │  │  Per-match Jobs  │    │
        │  │ ┌──────────────┐ │    │
        │  │ │ Static (once)│ │    │  → match_info.json
        │  │ │ Live  (30s)  │ │    │  → live/{ts}.json
        │  │ │ Score (60s)  │ │    │  → scorecard/{ts}.json
        │  │ └──────────────┘ │    │
        │  └──────────────────┘    │
        └──────────────────────────┘
                    │
        ┌───────────▼──────────────┐
        │       scraper/           │
        │  ┌─────────────────────┐ │
        │  │   BrowserPool       │ │  ← Semaphore(3 pages max)
        │  │   (browser.py)      │ │
        │  └────────┬────────────┘ │
        │           │              │
        │  ┌────────▼────────────┐ │
        │  │   APIInterceptor    │ │  ← Captures raw JSON from CREX API
        │  │   (interceptor.py)  │ │
        │  └────────┬────────────┘ │
        │           │              │
        │  ┌────────▼────────────┐ │
        │  │  match_list.py      │ │  fast-path: API JSON
        │  │  match_detail.py    │ │  fallback:  DOM parsing
        │  └─────────────────────┘ │
        └──────────────────────────┘
                    │
        ┌───────────▼──────────────┐
        │      storage/            │
        │  Atomic JSON writes      │
        │  output/{match_id}/      │
        └──────────────────────────┘
```

### Why Playwright + API Interception?

CREX is an **Angular SPA** — all content is rendered by JavaScript after API calls.
A plain `requests` scraper returns an empty HTML shell.

Our **hybrid strategy**:
1. Use Playwright (headless Chromium) to trigger page navigation and JavaScript execution.
2. Attach a network interceptor **before** navigation — capture the raw JSON payloads from CREX's internal REST API.
3. Parse the JSON directly (fast, structured, no HTML fragility).
4. Fall back to DOM parsing if the API response isn't captured (robustness).

This gives us **API speed** with **browser resilience**.

---

## Output Structure

```
output/
  schedule.json                    ← full fixture list snapshot
  {match_id}/
    match_info.json                ← venue, toss, umpires, result
    squads.json                    ← playing XI for both teams
    live_latest.json               ← most recent live snapshot
    scorecard_latest.json          ← most recent scorecard
    live/
      20260429T123000Z.json        ← timestamped live history
      20260429T123030Z.json
      ...
    scorecard/
      20260429T123000Z.json        ← timestamped scorecard history
      ...
```

### Sample `scorecard_latest.json`

```json
{
  "match_id": "abc123",
  "is_partial": false,
  "scraped_at": "2026-04-29T14:30:00Z",
  "innings": [
    {
      "innings_number": 1,
      "batting_team": "India",
      "bowling_team": "Australia",
      "total": "287/6",
      "overs": "50.0",
      "run_rate": 5.74,
      "batting": [
        {
          "player": "Rohit Sharma",
          "dismissal": "c Smith b Hazlewood",
          "runs": 83, "balls": 92,
          "fours": 7, "sixes": 2,
          "strike_rate": 90.22
        }
      ],
      "bowling": [...],
      "extras": {"wides": 4, "no_balls": 1, "byes": 2, "total": 7},
      "fall_of_wickets": [...]
    }
  ]
}
```

---

## Setup

### Prerequisites
- Python 3.10+
- Git

### Install

```bash
# Clone
git clone <your-repo-url>
cd crickey

# Create virtual environment
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (Chromium only)
playwright install chromium
```

---

## Usage

### Run the full scheduler (recommended)
Monitors the match list continuously and auto-triggers live/scorecard polling when matches start.

```bash
python main.py
```

### Single poll (fetch match list once and exit)
Useful for testing or one-off data collection.

```bash
python main.py --once
```

### Scrape a specific match
```bash
# Scrape all tabs
python main.py --match "ind-vs-aus-1st-odi-abc123"

# Scrape specific tabs only
python main.py --match "ind-vs-aus-1st-odi-abc123" --tabs info squads
python main.py --match "ind-vs-aus-1st-odi-abc123" --tabs scorecard live
```

---

## Configuration

All tuneable constants are in `config.py`:

| Constant | Default | Description |
|---|---|---|
| `SCHEDULE_POLL_INTERVAL` | 300s | How often to refresh the fixture list |
| `LIVE_POLL_INTERVAL` | 30s | How often to poll live score during a match |
| `SCORECARD_POLL_INTERVAL` | 60s | How often to poll scorecard during a match |
| `PRE_WARM_SECONDS` | 120s | Start polling this many seconds before kick-off |
| `MAX_CONCURRENT_PAGES` | 3 | Max simultaneous Playwright pages |
| `HEADLESS` | True | Run browser without UI |
| `MAX_RETRIES` | 3 | Retry attempts before giving up |

---

## Design Decisions & Tradeoffs

| Decision | Rationale |
|---|---|
| **Playwright over requests** | CREX requires JS execution (Angular SPA) |
| **API interception > DOM parsing** | Raw JSON is faster, more reliable, schema-stable |
| **APScheduler (not Celery)** | No Redis/broker needed; single-process asyncio is sufficient |
| **Pydantic models** | Schema validation catches malformed data at parse time |
| **Atomic file writes** | `tmp → os.replace()` prevents corrupt JSON on process kill |
| **Semaphore(3 pages)** | Caps memory/CPU use while allowing concurrency |
| **Per-match timestamped files** | Full audit trail; `latest.json` for quick access |

---

## Edge Cases Handled

- ✅ **TBD matches** — start_time stored as `null`
- ✅ **Squad not announced** — `announced: false`, empty player lists
- ✅ **Test matches** — multiple innings (innings array)
- ✅ **Super Over** — detected and stored as separate innings (`is_super_over: true`)
- ✅ **DLS applied** — `dls_target` field captured
- ✅ **Abandoned / No Result** — status mapped, final scrape still runs
- ✅ **Timezone differences** — all times normalised to UTC
- ✅ **Page load timeout** — retried with exponential back-off
- ✅ **API response unavailable** — automatic DOM fallback
- ✅ **Runaway pollers** — jobs cancelled immediately on match completion
- ✅ **Process killed mid-write** — atomic writes prevent corruption

---

## Potential Optimisations (given more time)

1. **Full API reverse-engineering** — eliminate Playwright for static data entirely, use `httpx` async
2. **Shared browser contexts** — reduce Playwright overhead by sharing one browser with many contexts
3. **WebSocket detection** — CREX may push live data via WS; subscribing would eliminate polling
4. **Redis + Celery** — distribute scraping jobs across machines for scale
5. **PostgreSQL** — replace JSON files for queryable historical analytics
6. **FastAPI layer** — expose scraped data via REST API
7. **Docker** — containerise for reproducible deployment

---

## Project Structure

```
crickey/
├── main.py              # Entry point & CLI
├── scheduler.py         # Job lifecycle orchestrator
├── config.py            # All constants
├── requirements.txt
├── scraper/
│   ├── browser.py       # Playwright browser pool
│   ├── interceptor.py   # Network API capture
│   ├── match_list.py    # Fixture list scraper
│   └── match_detail.py  # Per-tab match scrapers
├── models/
│   └── match.py         # Pydantic data schemas
├── storage/
│   └── json_store.py    # Atomic JSON persistence
├── utils/
│   ├── logger.py        # Loguru structured logging
│   ├── retry.py         # Exponential back-off decorator
│   └── time_utils.py    # Timezone-aware date parsers
└── output/              # Auto-created scraped data
```
