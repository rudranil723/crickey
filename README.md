# Crickey 🏑

A production-grade **real-time cricket data scraping system** for [CREX](https://crex.com).
Monitors the live fixture list, auto-triggers individual match scrapers when matches start,
and continuously polls **Live** and **Scorecard** data throughout each match.

> **Status (2026-04-29):** All 4 tabs confirmed working against a live ODI (NEP vs OMA).
> Scorecard delivered via `getSC4` API interception (2 innings). Live score via `getSV3`
> + `getBallFeeds`. Match Info and Squads via DOM (`.venue-detail`, `.playingxi-teams`).

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                      main.py                         │
│            (entry point / CLI / signal handling)     │
└───────────────────┬──────────────────────────────┘
                    │
        ┌───────────▼───────────┤
        │      scheduler.py        │
        │  APScheduler AsyncIO     │
        │  ┌──────────────────┐    │
        │  │ Match List Poller│◄───┼── every 5 min
        │  │  (every 5 min)   │    │
        │  └────────┬─────────┘    │
        │           │discovers     │
        │  ┌────────▼─────────┐    │
        │  │  Per-match Jobs  │    │
        │  │ ┌──────────────┐ │    │  → match_info.json
        │  │ │ Static (once)│ │    │  → squads.json
        │  │ │ Live  (30s)  │ │    │  → live/{ts}.json
        │  │ │ Score (60s)  │ │    │  → scorecard/{ts}.json
        │  │ └──────────────┘ │    │
        │  └──────────────────┘    │
        └──────────────────────────┘
                    │
        ┌───────────▼───────────┤
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
        ┌───────────▼───────────┤
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
2. Attach a network interceptor **before** navigation — capture raw JSON from CREX’s internal REST API (`api.goscorer.com`).
3. Parse the structured JSON directly (fast, no HTML fragility).
4. Fall back to DOM parsing if the API response isn’t captured.

This gives us **API speed** with **browser resilience**.

> See [DESIGN.md](DESIGN.md) for full rationale, resource optimisation strategy,
> and future architecture plans.

---

## Live Run Output

Confirmed run against **NEP vs OMA, 100th ODI, CWC League-2** (in-progress match,
2026-04-29):

```
[scrape_match_info]  Intercepted getSV3 → DOM scrape for Match Info → saved
[scrape_squads]      Intercepted getSV3 → DOM scrape for Squads → saved
[scrape_scorecard]   Intercepted getSV3 + getSC4 → 2 innings → saved
[scrape_live]        Intercepted getSV3 + getBallFeeds → score=205/4 rr=14.17 → saved
Total time: 33 seconds • 1 browser instance • 4 tabs
```

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

### Sample `match_info.json`

```json
{
  "match_id": "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD",
  "series": "CWC League-2 2023-27",
  "match_number": "100",
  "venue": "Tribhuvan University International Cricket Ground",
  "city": "Kirtipur",
  "date": "Wednesday, 29 April, 9:15 AM",
  "start_time_utc": "2026-04-29T03:45:00Z",
  "toss": "OMA won the toss and elected to bat",
  "umpires": ["Umpire Name 1", "Umpire Name 2"],
  "result": "Rain Stops Play"
}
```

### Sample `scorecard_latest.json`

```json
{
  "match_id": "nep-vs-oma-...",
  "is_partial": false,
  "innings": [
    {
      "innings_number": 1,
      "batting_team": "",
      "total": "305/8",
      "overs": "50.0",
      "batting": [
        {
          "player": "Aqib Ilyas",
          "runs": 83, "balls": 92,
          "fours": 7, "sixes": 2
        }
      ],
      "bowling": [...],
      "extras": {"wides": 4, "no_balls": 1, "byes": 2, "total": 7}
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
git clone <your-repo-url>
cd crickey

python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
playwright install chromium
```

---

## Usage

### Full scheduler (recommended)
Monitors the match list and auto-triggers live/scorecard polling when matches start.

```bash
python main.py
```

### Single poll (fetch match list once and exit)

```bash
python main.py --once
```

### Scrape a specific match

```bash
# All tabs
python main.py --match "ind-vs-aus-1st-odi-abc123"

# Specific tabs
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
| **APScheduler (not Celery)** | No Redis/broker needed; single-process asyncio |
| **Pydantic v2 models** | Schema validation catches malformed data at parse time |
| **Atomic file writes** | `tmp → os.replace()` prevents corrupt JSON on process kill |
| **Semaphore(3 pages)** | Caps memory/CPU while allowing concurrency |
| **Per-match timestamped files** | Full audit trail; `latest.json` for quick access |
| **Squads from /match-details DOM** | `/match-squads` URL renders a blank page on CREX |

> Full design rationale and future optimisation plan: **[DESIGN.md](DESIGN.md)**

---

## Edge Cases Handled

- ✅ **TBD matches** — `start_time` stored as `null`
- ✅ **Squad not announced** — `announced: false`, empty player lists
- ✅ **Test matches** — multiple innings (innings array)
- ✅ **Super Over** — detected as separate innings (`is_super_over: true`)
- ✅ **DLS applied** — `dls_target` field captured
- ✅ **Abandoned / No Result** — status mapped, final scrape still runs
- ✅ **All times in UTC** — epoch ms from `getSV3["mt"]` converted to ISO 8601
- ✅ **Status text from API** — `getSV3["B"]` carries human-readable text (e.g. “Rain Stops Play”)
- ✅ **Page load timeout** — retried with exponential back-off
- ✅ **API response unavailable** — automatic DOM fallback
- ✅ **Runaway pollers** — jobs cancelled immediately on match completion
- ✅ **Process killed mid-write** — atomic writes prevent corruption

---

## Project Structure

```
crickey/
├── main.py              # Entry point & CLI
├── scheduler.py         # Job lifecycle orchestrator
├── config.py            # All constants
├── requirements.txt
├── DESIGN.md            # Architecture decisions & follow-up Q&A
├── scraper/
│   ├── browser.py       # Playwright browser pool
│   ├── interceptor.py   # Network API capture
│   ├── match_list.py    # Fixture list scraper
│   └── match_detail.py  # Per-tab match scrapers
├── models/
│   └── match.py         # Pydantic v2 data schemas
├── storage/
│   └── json_store.py    # Atomic JSON persistence
├── utils/
│   ├── logger.py        # Loguru structured logging
│   ├── retry.py         # Exponential back-off decorator
│   └── time_utils.py    # Timezone-aware date parsers
└── output/              # Auto-created scraped data
```
