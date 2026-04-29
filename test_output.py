"""
test_output.py
--------------
Verifies the quality of scraped JSON output files.
Run after any scrape to confirm data is correctly structured.

Usage:
    python test_output.py                          # checks schedule only
    python test_output.py <match-slug>             # checks all 4 match tabs

Example:
    python test_output.py nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD
"""

import json
import os
import sys

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
WARN = "\033[93m WARN\033[0m"


def check(label: str, condition: bool, value=None, warn_only=False) -> bool:
    tag = PASS if condition else (WARN if warn_only else FAIL)
    suffix = f"  -> {value}" if value is not None else ""
    print(f"  [{tag}] {label}{suffix}")
    return condition


def load(path: str):
    if not os.path.exists(path):
        print(f"  [{FAIL}] File not found: {path}")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── Schedule ─────────────────────────────────────────────────────────────────

def test_schedule():
    print("\n=== schedule.json ===")
    d = load("output/schedule.json")
    if d is None:
        return

    check("Is a list", isinstance(d, list))
    check("Has 5+ matches", len(d) >= 5, f"{len(d)} matches")

    if d:
        print(f"\n  First 3 matches:")
        for m in d[:3]:
            ta = m.get("team_a", "?")
            tb = m.get("team_b", "?")
            st = m.get("status", "?")
            dt = m.get("start_time_utc", None)
            sr = m.get("series", "")
            slug = m.get("match_id", "")[:55]
            print(f"    [{st}] {ta} vs {tb}")
            print(f"           Series : {sr}")
            print(f"           Start  : {dt}")
            print(f"           Slug   : {slug}")
            print()
            check("team_a not empty", bool(ta and ta != "?"))
            check("team_b not empty", bool(tb and tb != "?"))
            check("match_id not empty", bool(slug))


# ─── Match Info ───────────────────────────────────────────────────────────────

def test_match_info(slug: str):
    print("\n=== match_info.json ===")
    d = load(f"output/{slug}/match_info.json")
    if d is None:
        return

    check("series present",     bool(d.get("series")),       d.get("series"))
    check("venue present",      bool(d.get("venue")),        d.get("venue"))
    check("city present",       bool(d.get("city")),         d.get("city"))
    check("date present",       bool(d.get("date")),         d.get("date"))
    check("start_time_utc",     bool(d.get("start_time_utc")), d.get("start_time_utc"))
    check("toss present",       bool(d.get("toss")),         d.get("toss"),       warn_only=True)
    check("umpires present",    bool(d.get("umpires")),      d.get("umpires"),    warn_only=True)
    check("result/status",      bool(d.get("result")),       d.get("result"),     warn_only=True)


# ─── Squads ───────────────────────────────────────────────────────────────────

def test_squads(slug: str):
    print("\n=== squads.json ===")
    d = load(f"output/{slug}/squads.json")
    if d is None:
        return

    announced = d.get("announced", False)
    teams = d.get("teams", [])
    check("announced flag",   announced, warn_only=True)
    check("2 teams present",  len(teams) == 2, f"{len(teams)} teams")

    for team in teams:
        name    = team.get("team_name", "?")
        players = team.get("players", [])
        print(f"\n  Team: {name} ({len(players)} players)")
        check("11 players",       len(players) == 11, f"{len(players)}", warn_only=True)
        check("has player names", all(p.get("name") for p in players))
        if players:
            print(f"    Players: {', '.join(p['name'] for p in players[:5])}...")


# ─── Scorecard ────────────────────────────────────────────────────────────────

def test_scorecard(slug: str):
    print("\n=== scorecard_latest.json ===")
    d = load(f"output/{slug}/scorecard_latest.json")
    if d is None:
        return

    innings = d.get("innings", [])
    check("has innings",     len(innings) >= 1, f"{len(innings)} innings")
    check("not partial",     not d.get("is_partial", True), warn_only=True)

    for inn in innings:
        n   = inn.get("innings_number", "?")
        tot = inn.get("total", "?")
        ovs = inn.get("overs", "?")
        bat = inn.get("batting", [])
        bwl = inn.get("bowling", [])
        ext = inn.get("extras", {})
        print(f"\n  Innings {n}: {tot} ({ovs} ov)")
        check("has batting rows",   len(bat) >= 1,  f"{len(bat)} batters")
        check("has bowling rows",   len(bwl) >= 1,  f"{len(bwl)} bowlers", warn_only=True)
        check("extras total >=0",   (ext.get("total") or 0) >= 0)
        if bat:
            top = bat[0]
            print(f"    Top batter : {top.get('player')} "
                  f"{top.get('runs')}({top.get('balls')}) "
                  f"4s:{top.get('fours')} 6s:{top.get('sixes')}")
        if bwl:
            top = bwl[0]
            print(f"    Top bowler : {top.get('player')} "
                  f"{top.get('wickets')}/{top.get('runs')} "
                  f"({top.get('overs')} ov)")


# ─── Live ─────────────────────────────────────────────────────────────────────

def test_live(slug: str):
    print("\n=== live_latest.json ===")
    d = load(f"output/{slug}/live_latest.json")
    if d is None:
        return

    check("current_score present",  bool(d.get("current_score")),  d.get("current_score"))
    check("current_overs present",  bool(d.get("current_overs")),  d.get("current_overs"))
    check("run_rate present",       d.get("run_rate") is not None,  d.get("run_rate"),    warn_only=True)
    check("status_text present",    bool(d.get("status_text")),     d.get("status_text"))

    batters = d.get("batters_on_crease", [])
    check("batters on crease", len(batters) >= 1, f"{len(batters)} batters", warn_only=True)
    for b in batters:
        print(f"    {b.get('player')} {b.get('runs')}({b.get('balls')}) SR:{b.get('strike_rate')}")

    bowler = d.get("current_bowler")
    if bowler:
        print(f"    Bowler: {bowler.get('player')} {bowler.get('wickets')}/{bowler.get('runs')} ({bowler.get('overs')} ov)")

    balls = d.get("recent_balls", [])
    check("recent balls present", len(balls) >= 1, f"{len(balls)} balls", warn_only=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else None

    test_schedule()

    if slug:
        test_match_info(slug)
        test_squads(slug)
        test_scorecard(slug)
        test_live(slug)
    else:
        print("\nTip: pass a match slug to also check match tabs:")
        print("  python test_output.py <match-slug>")

    print("\nDone.")
