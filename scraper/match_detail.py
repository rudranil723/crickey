"""
scraper/match_detail.py
-----------------------
Scrapes all four tabs for a single match:
  • Match Info  (/match-details)
  • Squads      (/match-details — Playing XI section, same page as info)
  • Live        (/ default — Summary/Commentary)
  • Scorecard   (/match-scorecard)

API key schema (reverse-engineered from api.goscorer.com):
  getSV3       — live match summary (single dict, minified keys)
  getSC4       — scorecard (list of innings, minified keys)
  getBallFeeds — ball-by-ball commentary

getSV3 confirmed key map (from live interception 2026-04-29):
  fo   = format          e.g. "ODI"
  mn   = match number    e.g. 100
  mt   = start time epoch ms
  B    = result text     e.g. "Rain Stops Play"
  fsr  = status code     "P"=In Progress, "C"=Completed, "U"=Upcoming
  ats  = current score   e.g. "205/4"
  a    = current overs   e.g. 19.15
  q    = run rate        e.g. "14.17*"
  t    = "target.rrr..."
  j    = team1 score     e.g. "305/8(50.0"
  k    = team2 score     e.g. "155/7(36.5"
  y    = batter1        "key.runs.balls.fours"
  z    = batter2        "key.runs.balls.fours"
  s    = strike rates   "sr1.sr2"
  x    = bowler         "key.wkts.overs.runs..."
  l/m/n= last 3 overs  "over_no:ball.ball.ball..."
  rb   = recent balls list
  g    = team keys      "teamAkey.teamBkey"
  sf   = series key
  fid  = series/fixture ID

getSC4 innings key map:
  a  = batting list  ["playerKey.runs.balls.fours.sixes", ...]
  b  = bowling list  ["playerKey.runs.balls.4s.6s.s1.s2.wkts.b1.b2/sr-econ/", ...]
  d  = total score   "305/8(300"  (parens = balls bowled)
  e  = extras        "wides.noballs.byes.legbyes.penalty"
  st = innings number

DOM selectors confirmed from debug_dom_dump (2026-04-29):
  .venue-detail         — "Date\nVenue, City\nBroadcaster" (3 lines)
  .series-name          — series name text
  .toss-wrap            — toss description
  .umpire-key           — umpire role labels
  .umpire-val           — umpire name values
  .match-name           — match number / description
  .team-result .font3   — result / status text
  .playingxi-teams      — playing XI container (on match-details page)
  .player-card          — individual player card
  .player-name          — player name within card
  .all-team-txt         — team name heading inside playing XI section
"""

from __future__ import annotations

import asyncio
import datetime
import random
import re
from typing import Any, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

import config
from models import (
    BattingEntry, BowlingEntry, Ball,
    Extras, FallOfWicket, Innings,
    LiveScore, MatchInfo, Player,
    Scorecard, Squads, TeamSquad,
)
from scraper.browser import pool
from scraper.interceptor import APIInterceptor
from utils.logger import log
from utils.retry import retry
from utils.time_utils import parse_crex_datetime, utc_now


# ─────────────────────────────────────────────────────────────────────────────
# Primitive helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _get(parts: list[str], idx: int, default: str = "") -> str:
    """Safe list index — returns default when out of range."""
    return parts[idx] if idx < len(parts) else default


async def _navigate(page: Page, url: str) -> None:
    try:
        await page.goto(url, wait_until="networkidle", timeout=config.PAGE_TIMEOUT_MS)
    except PlaywrightTimeout:
        log.warning("Timeout navigating to {} — continuing with partial DOM", url)


async def _text(page: Page, selector: str, default: str = "") -> str:
    try:
        el = await page.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return default


async def _texts(page: Page, selector: str) -> list[str]:
    try:
        els = await page.query_selector_all(selector)
        return [
            t for t in [(await el.inner_text()).strip() for el in els] if t
        ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Player key → name resolver  (built from getBallFeeds)
# ─────────────────────────────────────────────────────────────────────────────

def _build_key_map(ball_feeds: list[dict]) -> dict[str, str]:
    """
    Build {playerKey: playerName} from getBallFeeds entries.
    Over summaries (type='o') carry full names; ball entries (type='b')
    carry short commentary strings like "Bowler to Batter".
    """
    key_map: dict[str, str] = {}
    for item in ball_feeds:
        t = item.get("type")
        if t == "o":
            for key_field, name_field in [("pf1", "p1"), ("pf2", "p2"), ("bf", "bowler")]:
                k = str(item.get(key_field, "")).strip()
                n = str(item.get(name_field, "")).strip()
                if k and n:
                    key_map[k] = n
        elif t == "b":
            c1 = item.get("c1", "")
            bf = str(item.get("bf", "")).strip()
            pf = str(item.get("pf", "")).strip()
            m = re.match(r"^(.+?)\s+to\s+(.+)$", c1)
            if m:
                if bf:
                    key_map.setdefault(bf, m.group(1).strip())
                if pf:
                    key_map.setdefault(pf, m.group(2).strip())
    return key_map


def _resolve(key: str, key_map: dict[str, str]) -> str:
    return key_map.get(key, key)


# ─────────────────────────────────────────────────────────────────────────────
# getSV3 helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_score(raw: str) -> tuple[str, str]:
    """'305/8(50.0' → ('305/8', '50.0')"""
    if not raw:
        return "", ""
    m = re.match(r"^([\d/]+)\(?([0-9.]*)", raw)
    if m:
        return m.group(1), m.group(2)
    return raw, ""


_STATUS_MAP = {
    "P": "In Progress", "C": "Completed", "U": "Upcoming",
    "S": "Stumps",      "L": "Lunch",     "T": "Tea",
    "I": "Innings Break", "A": "Abandoned", "D": "Draw", "N": "No Result",
}


def _epoch_to_dt(ms: Any) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.utcfromtimestamp(
            int(ms) / 1000).replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def _parse_sv3_live(sv3: dict, key_map: dict[str, str], match_id: str) -> LiveScore:
    y_parts  = (sv3.get("y") or "").split(".")
    z_parts  = (sv3.get("z") or "").split(".")
    sr_parts = (sv3.get("s") or "").replace("*", "").split(".")

    batters: list[BattingEntry] = []
    for idx, parts in enumerate([y_parts, z_parts]):
        if parts and parts[0]:
            batters.append(BattingEntry(
                player=_resolve(parts[0], key_map),
                runs=_safe_int(_get(parts, 1)),
                balls=_safe_int(_get(parts, 2)),
                fours=_safe_int(_get(parts, 3)),
                strike_rate=_safe_float(_get(sr_parts, idx)),
            ))

    x_parts = (sv3.get("x") or "").split(".")
    bowler: Optional[BowlingEntry] = None
    if x_parts and x_parts[0]:
        bowler = BowlingEntry(
            player=_resolve(x_parts[0], key_map),
            wickets=_safe_int(_get(x_parts, 1)),
            overs=_safe_float(_get(x_parts, 2)),
            runs=_safe_int(_get(x_parts, 3)),
        )

    recent_balls: list[Ball] = []
    for inn_rb in (sv3.get("rb") or []):
        for ball in (inn_rb.get("b") or []):
            d = ball.get("d", 0)
            u = str(ball.get("u", ""))
            recent_balls.append(Ball(
                over="", runs=_safe_int(d) or 0,
                is_wicket=(u == "W"),
                is_boundary=(d == 4), is_six=(d == 6),
                commentary="",
            ))

    over_summaries = []
    for key in ["l", "m", "n"]:
        val = sv3.get(key, "")
        if val:
            parts = val.split(":", 1)
            over_summaries.append(f"Ov {parts[0]}: {parts[1] if len(parts) > 1 else ''}")

    t_parts = (sv3.get("t") or "").split(".")
    rrr = _safe_float(_get(t_parts, 1))

    # Derive team names from j/k score strings via g key ("teamAkey.teamBkey")
    g_parts = (sv3.get("g") or "").split(".")
    team1_key = _get(g_parts, 0)
    team2_key = _get(g_parts, 1)

    # Task 4a: Detect rain delay / DLS events from the sv3 "B" result text.
    # sv3["B"] contains the human-readable status string from the API (e.g. "Rain Stops Play",
    # "DLS Target Revised"). We check for relevant keywords and expose them as interruption_reason.
    b_text = (sv3.get("B") or "").lower()
    _INTERRUPTION_KEYWORDS = ("rain", "dls", "reduced", "delay", "wet", "light", "bad light")
    interruption_reason: Optional[str] = None
    if any(kw in b_text for kw in _INTERRUPTION_KEYWORDS):
        interruption_reason = sv3.get("B")  # raw string, e.g. "Rain Stops Play"

    return LiveScore(
        match_id=match_id,
        status_text=(
            sv3.get("B") or  # e.g. "Rain Stops Play"
            _STATUS_MAP.get(sv3.get("fsr", ""), sv3.get("fsr", ""))
        ),
        batting_team=_resolve(team1_key, key_map) if sv3.get("h") == 1 else _resolve(team2_key, key_map),
        bowling_team="",
        current_score=sv3.get("ats") or "",
        current_overs=str(sv3.get("a") or ""),
        run_rate=_safe_float((sv3.get("q") or "").rstrip("*")),
        required_run_rate=rrr,
        last_5_overs=" | ".join(over_summaries),
        current_partnership="",
        batters_on_crease=batters,
        current_bowler=bowler,
        recent_balls=recent_balls,
        interruption_reason=interruption_reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# getSC4 helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_batting_str(raw: str, key_map: dict[str, str]) -> Optional[BattingEntry]:
    parts = raw.split(".")
    if not parts or not parts[0]:
        return None
    return BattingEntry(
        player=_resolve(parts[0], key_map),
        runs=_safe_int(_get(parts, 1)),
        balls=_safe_int(_get(parts, 2)),
        fours=_safe_int(_get(parts, 3)),
        sixes=_safe_int(_get(parts, 4)),
        strike_rate=None,
    )


def _parse_bowling_str(raw: str, key_map: dict[str, str]) -> Optional[BowlingEntry]:
    """
    Format: 'playerKey.runs.balls.4s.6s.s1.s2.wkts.b1.b2/sr-econ/'
    All fields after playerKey are optional — handled via _get().
    """
    if not raw or not raw.strip():
        return None
    sr, econ = None, None
    slash_m = re.search(r"/([0-9.]+)-([0-9.]+)/", raw)
    if slash_m:
        econ = _safe_float(slash_m.group(2))
    main = raw.split("/")[0].strip()
    parts = main.split(".")
    if not parts or not parts[0]:
        return None
    balls_raw = _safe_int(_get(parts, 2))
    overs = round(balls_raw / 6, 1) if balls_raw is not None else None
    return BowlingEntry(
        player=_resolve(parts[0], key_map),
        runs=_safe_int(_get(parts, 1)),
        overs=overs,
        wickets=_safe_int(_get(parts, 7)),
        economy=econ,
    )


def _parse_extras_str(raw: str) -> Extras:
    parts = (raw or "0.0.0.0.0").split(".")
    vals = [_safe_int(_get(parts, i)) or 0 for i in range(5)]
    return Extras(
        wides=vals[0], no_balls=vals[1], byes=vals[2],
        leg_byes=vals[3], penalty=vals[4], total=sum(vals),
    )


def _parse_sc4_innings(raw: dict, inn_num: int, key_map: dict[str, str]) -> Innings:
    batting = [e for e in (_parse_batting_str(s, key_map) for s in (raw.get("a") or [])) if e]
    bowling = [e for e in (_parse_bowling_str(s, key_map) for s in (raw.get("b") or [])) if e]
    total_runs, total_overs = _decode_score(raw.get("d") or "")
    overs_balls = _safe_int(total_overs)
    if overs_balls and overs_balls > 100:
        total_overs = str(round(overs_balls / 6, 1))
    return Innings(
        innings_number=_safe_int(raw.get("st")) or inn_num,
        batting_team="", bowling_team="",
        total=total_runs, overs=total_overs,
        extras=_parse_extras_str(raw.get("e") or ""),
        batting=batting, bowling=bowling, is_super_over=False,
    )


def _parse_sc4(sc4: list, key_map: dict[str, str], match_id: str) -> Scorecard:
    return Scorecard(
        match_id=match_id,
        innings=[_parse_sc4_innings(inn, i + 1, key_map)
                 for i, inn in enumerate(sc4) if isinstance(inn, dict)],
        is_partial=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# getBallFeeds helper
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ball_feeds(feeds: list, match_id: str) -> list[Ball]:
    balls: list[Ball] = []
    for item in feeds:
        t = item.get("type")
        if t == "b":
            runs_raw = item.get("b", 0)
            runs = _safe_int(runs_raw) or 0
            balls.append(Ball(
                over=str(item.get("o") or ""),
                runs=runs,
                is_wicket=(str(runs_raw) == "W"),
                is_boundary=(runs == 4), is_six=(runs == 6),
                commentary=str(item.get("c1") or ""),
            ))
        elif t == "o":
            balls.append(Ball(
                over=str(item.get("o") or ""),
                runs=_safe_int(item.get("runs")) or 0,
                commentary=(
                    f"End of over {item.get('o')}: "
                    f"{item.get('team', '')} {item.get('s', '')} — "
                    f"{item.get('bowler', '')} {item.get('rb', '')}"
                ),
            ))
    return balls


# ─────────────────────────────────────────────────────────────────────────────
# DOM scrapers  (confirmed selectors from debug_dom_dump 2026-04-29)
# ─────────────────────────────────────────────────────────────────────────────

async def _parse_info_dom(page: Page, match_id: str, sv3: Optional[dict] = None) -> MatchInfo:
    """
    Parse match info from the rendered match-details DOM.

    .venue-detail  → "Date\nVenue, City\nBroadcaster"  (3 newline-separated lines)
    .series-name   → series name
    .toss-wrap     → toss description
    .umpire-val    → umpire names (multiple elements)
    .match-name    → match number / description
    .team-result .font3  → result text
    """
    log.debug("[{}] DOM scrape for Match Info", match_id)

    # --- venue-detail: "Date\nVenue, City\nBroadcaster" ---
    venue_raw = await _text(page, ".venue-detail")
    lines = [l.strip() for l in venue_raw.split("\n") if l.strip()]
    date_str   = lines[0] if len(lines) > 0 else ""
    venue_city = lines[1] if len(lines) > 1 else ""
    # Split "Tribhuvan University International Cricket Ground, Kirtipur"
    venue_name, city = (venue_city.rsplit(",", 1) if "," in venue_city
                        else (venue_city, ""))
    venue_name = venue_name.strip()
    city       = city.strip()

    # --- series ---
    series = await _text(page, ".series-name")

    # --- match number: prefer sv3 mn, fallback to .match-name DOM ---
    match_number = ""
    if sv3:
        match_number = str(sv3.get("mn") or "")
    if not match_number:
        match_number = await _text(page, ".match-name, .format-match-exp")

    # --- toss ---
    toss = await _text(page, ".toss-wrap")

    # --- umpires: .umpire-val elements ---
    umpires = await _texts(page, ".umpire-val")

    # --- result: prefer sv3 B key, fallback to DOM ---
    result = ""
    if sv3:
        result = sv3.get("B") or _STATUS_MAP.get(sv3.get("fsr", ""), "")
    if not result:
        result = await _text(page, ".team-result .font3")

    # --- start time: from sv3 mt epoch ms ---
    start_dt = _epoch_to_dt(sv3.get("mt")) if sv3 else None
    if not start_dt and date_str:
        start_dt = parse_crex_datetime(date_str)

    return MatchInfo(
        match_id=match_id,
        series=series or "",
        match_number=match_number or "",
        venue=venue_name or None,
        city=city or None,
        date=date_str,
        start_time_utc=start_dt,
        toss=toss or None,
        umpires=umpires,
        match_referee=None,
        result=result or None,
        player_of_match=None,
    )


async def _parse_squads_dom(page: Page, match_id: str) -> Squads:
    """
    Scrape Playing XI from the match-details page.
    CREX renders squads inside .playingxi-teams on the same page as match info.

    Structure confirmed from debug_dom_dump:
      .playingxi-teams          — outer container
        .all-team-txt           — team name heading (one per team block)
        .player-card            — individual player card
          .player-name          — player's name text
    """
    log.debug("[{}] DOM scrape for Squads", match_id)
    teams: list[TeamSquad] = []

    # Each team's playing XI is in a separate .playingxi-teams block
    pxi_sections = await page.query_selector_all(".playingxi-teams")

    if not pxi_sections:
        # Fallback: try to find any team-named section with player cards
        log.debug("[{}] .playingxi-teams not found, trying fallback selectors", match_id)
        pxi_sections = await page.query_selector_all(
            "[class*='playing-xi'], [class*='squad'], [class*='playing11']"
        )

    for section in pxi_sections:
        # Team name
        team_el = await section.query_selector(".all-team-txt, .team-name, h3, h4")
        team_name = (await team_el.inner_text()).strip() if team_el else "Unknown"

        # Player cards
        player_els = await section.query_selector_all(".player-card")
        players: list[Player] = []
        for card in player_els:
            name_el = await card.query_selector(".player-name")
            if not name_el:
                # fallback: first span/div with non-empty text
                name_el = await card.query_selector("span, div")
            if name_el:
                name = (await name_el.inner_text()).strip()
                if name:
                    # Detect captain/keeper from icon classes or text
                    card_html = await card.inner_html()
                    is_captain = "captain" in card_html.lower() or "(c)" in name.lower()
                    is_keeper  = "keeper" in card_html.lower()  or "(wk)" in name.lower()
                    clean_name = re.sub(r"\s*\(c\)|\s*\(wk\)", "", name, flags=re.I).strip()
                    players.append(Player(
                        name=clean_name,
                        is_captain=is_captain,
                        is_keeper=is_keeper,
                    ))

        if team_name or players:
            teams.append(TeamSquad(team_name=team_name, players=players))

    return Squads(match_id=match_id, teams=teams, announced=bool(teams))


async def _parse_scorecard_dom(page: Page, match_id: str) -> Scorecard:
    log.debug("[{}] DOM fallback for Scorecard", match_id)
    innings_list: list[Innings] = []
    sections = await page.query_selector_all(
        "[class*='team-inning'], [class*='scorecard-table'], [class*='innings-container']"
    )
    for idx, section in enumerate(sections, start=1):
        bt_el = await section.query_selector(".team-name, [class*='batting-team']")
        batting_team = (await bt_el.inner_text()).strip() if bt_el else ""
        batting_rows = await section.query_selector_all(
            "[class*='player-data'], [class*='batting-row'], tbody tr"
        )
        batting: list[BattingEntry] = []
        for row in batting_rows:
            cells = await row.query_selector_all("td")
            texts = [(await c.inner_text()).strip() for c in cells]
            if len(texts) >= 5 and texts[0]:
                batting.append(BattingEntry(
                    player=texts[0],
                    dismissal=texts[1] if len(texts) > 1 else None,
                    runs=_safe_int(texts[2]) if len(texts) > 2 else None,
                    balls=_safe_int(texts[3]) if len(texts) > 3 else None,
                    fours=_safe_int(texts[4]) if len(texts) > 4 else None,
                    sixes=_safe_int(texts[5]) if len(texts) > 5 else None,
                ))
        if batting:
            innings_list.append(Innings(
                innings_number=idx, batting_team=batting_team,
                bowling_team="", batting=batting, bowling=[],
            ))
    # NOTE (Task 4d): is_partial=True signals that this scorecard was scraped mid-innings
    # (DOM fallback path). The scheduler sets is_partial=False only after the final
    # authoritative scrape via _job_final_scorecard(), confirming innings are complete.
    return Scorecard(match_id=match_id, innings=innings_list, is_partial=True)


async def _parse_live_dom(page: Page, match_id: str) -> LiveScore:
    log.debug("[{}] DOM fallback for Live", match_id)
    status = await _text(page, ".team-result .font3, [class*='match-status']")
    score  = await _text(page, ".team-score, [class*='live-score']")
    overs  = await _text(page, ".team-over, [class*='overs']")
    els = await page.query_selector_all(
        "[class*='cm-b-ballupdate'], [class*='commentary-item'], [class*='ball-item']"
    )
    balls = [
        Ball(over="", runs=0, commentary=(await el.inner_text()).strip())
        for el in els[:20]
        if (await el.inner_text()).strip()
    ]
    return LiveScore(
        match_id=match_id, status_text=status, batting_team="", bowling_team="",
        current_score=score or None, current_overs=overs or None, recent_balls=balls,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public scrapers
# ─────────────────────────────────────────────────────────────────────────────

@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_match_info(match_id: str, slug: str) -> MatchInfo:
    url = f"{config.MATCH_DETAIL_BASE}/{slug}/match-details"
    log.info("[{}] Scraping Match Info → {}", match_id, url)
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await _navigate(page, url)
        await asyncio.sleep(random.uniform(config.MIN_REQUEST_DELAY, config.MAX_REQUEST_DELAY))
        sv3 = interceptor.find("getSV3")  # provides mn, mt, B, fsr
        # Always use DOM for venue/series/toss/umpires; sv3 enriches start_time + result
        return await _parse_info_dom(page, match_id, sv3=sv3)


@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_squads(match_id: str, slug: str) -> Squads:
    """
    Squads are scraped from match-details page (.playingxi-teams).
    The /match-squads URL renders a blank page (no API, no DOM content).
    """
    url = f"{config.MATCH_DETAIL_BASE}/{slug}/match-details"
    log.info("[{}] Scraping Squads → {}", match_id, url)
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await _navigate(page, url)
        await asyncio.sleep(random.uniform(config.MIN_REQUEST_DELAY, config.MAX_REQUEST_DELAY))
        return await _parse_squads_dom(page, match_id)


@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_scorecard(match_id: str, slug: str) -> Scorecard:
    url = f"{config.MATCH_DETAIL_BASE}/{slug}/match-scorecard"
    log.info("[{}] Scraping Scorecard → {}", match_id, url)
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await _navigate(page, url)
        await asyncio.sleep(random.uniform(config.MIN_REQUEST_DELAY, config.MAX_REQUEST_DELAY))
        sc4     = interceptor.find("getSC4")
        feeds   = interceptor.find("getBallFeeds") or []
        key_map = _build_key_map(feeds) if isinstance(feeds, list) else {}
        if sc4 and isinstance(sc4, list) and sc4:
            log.info("[{}] Scorecard from getSC4: {} innings", match_id, len(sc4))
            return _parse_sc4(sc4, key_map, match_id)
        return await _parse_scorecard_dom(page, match_id)


@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_live(match_id: str, slug: str) -> LiveScore:
    url = f"{config.MATCH_DETAIL_BASE}/{slug}"
    log.info("[{}] Scraping Live score → {}", match_id, url)
    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await _navigate(page, url)
        await asyncio.sleep(random.uniform(2.5, 4.0))
        sv3     = interceptor.find("getSV3")
        feeds   = interceptor.find("getBallFeeds") or []
        key_map = _build_key_map(feeds) if isinstance(feeds, list) else {}
        if sv3 and isinstance(sv3, dict):
            live = _parse_sv3_live(sv3, key_map, match_id)
            if isinstance(feeds, list) and feeds:
                live.recent_balls = _parse_ball_feeds(feeds, match_id)[:20]
            if live.current_score:
                log.info("[{}] Live from getSV3: score={} rr={}",
                         match_id, live.current_score, live.run_rate)
                return live
        return await _parse_live_dom(page, match_id)


async def scrape_all_static(match_id: str, slug: str) -> dict:
    """Scrape Match Info and Squads concurrently on first match discovery."""
    info_task, squads_task = (
        asyncio.create_task(scrape_match_info(match_id, slug)),
        asyncio.create_task(scrape_squads(match_id, slug)),
    )
    info, squads = await asyncio.gather(info_task, squads_task, return_exceptions=True)
    return {
        "match_info": info   if not isinstance(info,   Exception) else None,
        "squads":     squads if not isinstance(squads, Exception)  else None,
    }
