"""
config.py
---------
Central configuration. All tuneable constants live here so they can be
adjusted without touching business logic.
"""

from __future__ import annotations

# ── URLs ──────────────────────────────────────────────────────────────────────
BASE_URL = "https://crex.com"
MATCH_LIST_URL = f"{BASE_URL}/fixtures/match-list"
SCHEDULE_URL   = f"{BASE_URL}/schedule"

# Pattern: /cricket-live-score/<slug>/match-details   (info)
#           /cricket-live-score/<slug>/match-scorecard (scorecard)
#           /cricket-live-score/<slug>                 (live/summary)
MATCH_DETAIL_BASE = f"{BASE_URL}/cricket-live-score"

# ── Polling Intervals (seconds) ───────────────────────────────────────────────
SCHEDULE_POLL_INTERVAL   = 300   # how often to refresh the full match list
LIVE_POLL_INTERVAL       = 30    # how often to poll live score during a match
SCORECARD_POLL_INTERVAL  = 60    # how often to poll scorecard during a match
PRE_WARM_SECONDS         = 120   # start scraping this many seconds before kick-off

# Structured poll interval map (used for documentation / future dynamic scheduling)
POLL_INTERVALS = {
    "pre_match":     SCHEDULE_POLL_INTERVAL,  # 300s — periodic fixture refresh
    "warmup":        60,                       # 60s  — in the 2-min pre-warm window
    "live_innings":  LIVE_POLL_INTERVAL,       # 30s  — active batting
    "innings_break": 120,                      # 120s — drinks break / innings change
    "rain_delay":    180,                      # 180s — DLS / weather delay
    "completed":     None,                     # no polling after match ends
}

# ── Browser / Concurrency ─────────────────────────────────────────────────────
HEADLESS                 = True
MAX_CONCURRENT_PAGES     = 3     # semaphore limit on simultaneous Playwright pages
PAGE_TIMEOUT_MS          = 30_000
NETWORK_IDLE_TIMEOUT_MS  = 10_000

# ── Retry / Back-off ─────────────────────────────────────────────────────────
MAX_RETRIES              = 3
RETRY_BASE_DELAY         = 2.0   # seconds; doubles each attempt (exponential back-off)

# ── Anti-detection ───────────────────────────────────────────────────────────
MIN_REQUEST_DELAY        = 1.0   # seconds between consecutive page navigations
MAX_REQUEST_DELAY        = 3.0
VIEWPORT                 = {"width": 1280, "height": 800}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Storage ───────────────────────────────────────────────────────────────────
OUTPUT_DIR               = "output"
SCHEDULE_FILE            = f"{OUTPUT_DIR}/schedule.json"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE                 = "logs/crickey.log"
LOG_LEVEL                = "DEBUG"
LOG_ROTATION             = "10 MB"
