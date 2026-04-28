# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

"""
check_imports.py
----------------
Quick smoke-test: verifies all modules import without errors
and that Pydantic models instantiate correctly.
Run with: python check_imports.py
"""

import sys

def check(name, fn):
    try:
        fn()
        print(f"  [OK]  {name}")
    except Exception as e:
        print(f"  [FAIL]  {name}  ->  {e}")
        sys.exit(1)

print("\n=== Crickey import check ===\n")

check("config", lambda: __import__("config"))
check("models.match", lambda: __import__("models.match"))
check("utils.logger", lambda: __import__("utils.logger"))
check("utils.retry", lambda: __import__("utils.retry"))
check("utils.time_utils", lambda: __import__("utils.time_utils"))
check("storage.json_store", lambda: __import__("storage.json_store"))
check("scraper.browser", lambda: __import__("scraper.browser"))
check("scraper.interceptor", lambda: __import__("scraper.interceptor"))
check("scraper.match_list", lambda: __import__("scraper.match_list"))
check("scraper.match_detail", lambda: __import__("scraper.match_detail"))
check("scheduler", lambda: __import__("scheduler"))
check("main", lambda: __import__("main"))

print()

# ── Pydantic model round-trip ─────────────────────────────────────────────────
from models import MatchSummary, Scorecard, Innings, BattingEntry, LiveScore
from datetime import datetime, timezone

def check_models():
    ms = MatchSummary(
        match_id="test123",
        match_slug="ind-vs-aus-test123",
        detail_url="https://crex.com/cricket-live-score/ind-vs-aus-test123",
        team_a="India",
        team_b="Australia",
        series_name="Test Series",
        match_type="Test",
        start_time=datetime(2026, 4, 29, 9, 0, tzinfo=timezone.utc),
        status="upcoming",
    )
    sc = Scorecard(
        match_id="test123",
        innings=[
            Innings(
                innings_number=1,
                batting_team="India",
                bowling_team="Australia",
                total="287/6",
                overs="50.0",
                batting=[BattingEntry(player="Rohit Sharma", runs=83, balls=92)],
            )
        ],
    )
    live = LiveScore(
        match_id="test123",
        status_text="India need 47 in 30 balls",
        batting_team="India",
        bowling_team="Australia",
        current_score="240/4",
    )
    # Serialise to dict (simulates saving to JSON)
    assert ms.model_dump(mode="json")["match_id"] == "test123"
    assert sc.innings[0].batting[0].player == "Rohit Sharma"
    assert live.current_score == "240/4"

check("Pydantic model round-trip", check_models)

# ── time_utils parse ──────────────────────────────────────────────────────────
from utils.time_utils import parse_crex_datetime

def check_time():
    dt = parse_crex_datetime("Apr 29, 2026, 02:30 PM IST")
    assert dt is not None, "Parsed datetime is None"
    assert dt.tzinfo is not None, "No tzinfo"
    assert dt.hour == 9, f"Expected 9 UTC, got {dt.hour}"   # 2:30 PM IST = 09:00 UTC

check("Time parser (IST → UTC)", check_time)

print("\n[ALL PASSED]  Project is ready to run.\n")
print("Next step:  python main.py --once")
print("            python main.py          (full scheduler)\n")
