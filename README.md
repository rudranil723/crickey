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
2. Attach a network interceptor **before** navigation — capture raw JSON from CREX's internal REST API (`api.goscorer.com`).
3. Parse the structured JSON directly (fast, no HTML fragility).
4. Fall back to DOM parsing if the API response isn't captured.

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

> Full sample: [`sample_output/match_info_sample.json`](sample_output/match_info_sample.json)

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

> Full sample: [`sample_output/scorecard_sample.json`](sample_output/scorecard_sample.json)

```json
{
  "match_id": "nep-vs-oma-...",
  "is_partial": false,
  "innings": [
    {
      "innings_number": 1,
      "batting_team": "Oman",
      "total": "305/8",
      "overs": "50.0",
      "batting": [
        { "player": "Aqib Ilyas", "runs": 83, "balls": 92, "fours": 7, "sixes": 2 }
      ],
      "bowling": [
        { "player": "Sagar Pun", "wickets": 3, "overs": 10.0, "runs": 52, "economy": 5.2 }
      ],
      "extras": { "wides": 6, "no_balls": 2, "byes": 3, "leg_byes": 1, "total": 12 }
    }
  ]
}
```

### Sample `live_latest.json`

> Full sample: [`sample_output/live_sample.json`](sample_output/live_sample.json)

```json
{
  "match_id": "nep-vs-oma-...",
  "status_text": "In Progress",
  "current_score": "205/4",
  "current_overs": "38.3",
  "run_rate": 5.33,
  "required_run_rate": 7.81,
  "interruption_reason": null,
  "batters_on_crease": [
    { "player": "Rohit Paudel",   "runs": 55, "balls": 60, "strike_rate": 91.7 },
    { "player": "Dipendra Airee", "runs": 18, "balls": 22, "strike_rate": 81.8 }
  ],
  "current_bowler": { "player": "Bilal Khan", "wickets": 2, "overs": 7.3, "runs": 31 },
  "recent_balls": [
    { "over": "38.3", "runs": 4, "is_boundary": true, "commentary": "FOUR! drives through covers" }
  ]
}
```

### Sample `squads.json`

> Full sample: [`sample_output/squads_sample.json`](sample_output/squads_sample.json)

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

### Environment Variables

Copy `.env.example` to `.env` and adjust values if needed:

```bash
cp .env.example .env
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

## Running Tests

```bash
python -m pytest tests/ -v
```

Or run the output validator manually after a scrape:

```bash
# Check schedule only
python -m pytest tests/test_output.py -v

# Check a specific match's output
python tests/test_output.py <match-slug>
```

---

## Configuration

All tuneable constants are in `config.py` (see `.env.example` for environment overrides):

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
| **Crash recovery state file** | `output/.scheduler_state.json` persists job registry across restarts |

> Full design rationale and future optimisation plan: **[DESIGN.md](DESIGN.md)**

---

## Edge Cases Handled

- ✅ **TBD matches** — `start_time` stored as `null`
- ✅ **Squad not announced** — `announced: false`, empty player lists
- ✅ **Test matches** — multiple innings (innings array)
- ✅ **Super Over** — detected as separate innings (`is_super_over: true`)
- ✅ **DLS applied** — `dls_target` field captured
- ✅ **Rain / DLS / Delay** — `interruption_reason` field set on `LiveScore`
- ✅ **Abandoned / No Result** — status mapped, final scrape still runs
- ✅ **All times in UTC** — epoch ms from `getSV3["mt"]` converted to ISO 8601
- ✅ **Status text from API** — `getSV3["B"]` carries human-readable text (e.g. "Rain Stops Play")
- ✅ **Page load timeout** — retried with exponential back-off
- ✅ **API response unavailable** — automatic DOM fallback
- ✅ **Runaway pollers** — jobs cancelled immediately on match completion
- ✅ **Process killed mid-write** — atomic writes prevent corruption
- ✅ **Scheduler crash recovery** — `output/.scheduler_state.json` restored on restart

---

## Project Structure

```
crickey/
├── main.py              # Entry point & CLI
├── scheduler.py         # Job lifecycle orchestrator
├── config.py            # All constants
├── requirements.txt
├── .env.example         # Environment variable template
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
├── tests/
│   ├── __init__.py
│   └── test_output.py   # Output quality validator
└── output/              # Auto-created scraped data (git-ignored)
```
