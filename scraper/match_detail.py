"""
scraper/match_detail.py
-----------------------
Scrapes all four tabs for a single match:
  • Match Info  (/match-details)
  • Squads      (/match-details — Playing XI section)
  • Live        (/ default — Summary/Commentary)
  • Scorecard   (/match-scorecard)

API key schema (reverse-engineered from api.goscorer.com):
  getSV3       — live match summary (single dict, minified single-letter keys)
  getSC4       — scorecard (list of innings, minified keys)
  getBallFeeds — ball-by-ball commentary (list, semi-readable keys)

getSV3 key reference:
  ats  = current batting team score  e.g. "164/3"
  j    = team 1 score                e.g. "305/8(50.0"
  k    = team 2 score                e.g. "120/6(30.3"
  fo   = format                      e.g. "ODI"
  mn   = match number                e.g. "100"
  mt   = match start epoch ms
  q    = current run rate            e.g. "6.8"
  t    = "target.rrr..."
  p    = current batter keys         e.g. "7SA.4NY"
  y    = batter1 stats               e.g. "XL.54.36.3"  key.runs.balls.fours
  z    = batter2 stats               e.g. "7UK.9.5.0"
  s    = strike rates                e.g. "37.41*"
  x    = current bowler              e.g. "10Y.14.22.106.168.0.2" key.wkts.overs.runs
  l/m/n= last 3 overs               e.g. "28:1.1.1.0.1.W"
  rb   = recent balls list          each: {bf: bowler_key, d: runs, t: type}
  fsr  = match status               "P"=in progress,"C"=complete,"U"=upcoming

getSC4 innings key reference:
  a  = batting list  "playerKey.runs.balls.fours.sixes"
  b  = bowling list  "playerKey.runs.balls_faced.4s.6s.score1.score2.wkts.b1.b2/sr-econ/"
  c  = extras total
  d  = total score   "305/8(300"  (overs as balls in parens)
  e  = extras detail "wides.noballs.byes.legbyes.penalty"
  p  = partnerships
  st = innings number "1" / "2"

Player key→name map is built from getBallFeeds commentary (no dedicated endpoint).
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
        return [(await el.inner_text()).strip() for el in els
                if (await el.inner_text()).strip()]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Player key → name resolver  (built from getBallFeeds)
# ─────────────────────────────────────────────────────────────────────────────

def _build_key_map(ball_feeds: list[dict]) -> dict[str, str]:
    """
    Build {playerKey: playerName} from getBallFeeds entries.

    Over summaries (type='o'): have full p1/p2/bowler names with pf1/pf2/bf keys.
    Ball entries  (type='b'): c1 = "BowlerShort to BatterShort"; bf/pf are keys.
    """
    key_map: dict[str, str] = {}

    for item in ball_feeds:
        t = item.get("type")

        if t == "o":
            pairs = [
                (item.get("pf1", "").strip(), item.get("p1", "").strip()),
                (item.get("pf2", "").strip(), item.get("p2", "").strip()),
                (item.get("bf",  "").strip(), item.get("bowler", "").strip()),
            ]
            for key, name in pairs:
                if key and name:
                    key_map[key] = name

        elif t == "b":
            c1 = item.get("c1", "")
            bf = item.get("bf", "").strip()
            pf = item.get("pf", "").strip()
            m = re.match(r"^(.+?)\s+to\s+(.+)$", c1)
            if m:
                if bf:
                    key_map.setdefault(bf, m.group(1).strip())
                if pf:
                    key_map.setdefault(pf, m.group(2).strip())

    return key_map


def _resolve(key: str, key_map: dict[str, str]) -> str:
    """Return player name for key, or the raw key if not found."""
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
    "S": "Stumps", "L": "Lunch", "T": "Tea", "I": "Innings Break",
    "A": "Abandoned", "D": "Draw", "N": "No Result",
}


def _parse_sv3_info(sv3: dict, match_id: str) -> MatchInfo:
    mt = sv3.get("mt")
    start_dt = None
    if mt:
        try:
            start_dt = datetime.datetime.utcfromtimestamp(mt / 1000).replace(
                tzinfo=datetime.timezone.utc)
        except Exception:
            pass

    return MatchInfo(
        match_id=match_id,
        series="",
        match_number=str(sv3.get("mn") or ""),
        venue=None,
        city=None,
        date="",
        start_time_utc=start_dt,
        toss=None,
        umpires=[],
        match_referee=None,
        result=_STATUS_MAP.get(sv3.get("fsr", ""), None),
        player_of_match=None,
    )


def _parse_sv3_live(sv3: dict, key_map: dict[str, str], match_id: str) -> LiveScore:
    # Batters
    y_parts = (sv3.get("y") or "").split(".")
    z_parts = (sv3.get("z") or "").split(".")
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

    # Bowler: key.wkts.overs.runs...
    x_parts = (sv3.get("x") or "").split(".")
    bowler: Optional[BowlingEntry] = None
    if x_parts and x_parts[0]:
        bowler = BowlingEntry(
            player=_resolve(x_parts[0], key_map),
            wickets=_safe_int(_get(x_parts, 1)),
            overs=_safe_float(_get(x_parts, 2)),
            runs=_safe_int(_get(x_parts, 3)),
        )

    # Recent balls from rb list
    recent_balls: list[Ball] = []
    for inn_rb in (sv3.get("rb") or []):
        for ball in (inn_rb.get("b") or []):
            d = ball.get("d", 0)
            u = str(ball.get("u", ""))
            recent_balls.append(Ball(
                over="",
                runs=_safe_int(d) or 0,
                is_wicket=(u == "W"),
                is_boundary=(d == 4),
                is_six=(d == 6),
                commentary="",
            ))

    # Last 3 overs text
    over_summaries = []
    for key in ["l", "m", "n"]:
        val = sv3.get(key, "")
        if val:
            parts = val.split(":", 1)
            over_summaries.append(f"Ov {parts[0]}: {parts[1] if len(parts) > 1 else ''}")

    # RRR from t field "target.rrr..."
    t_parts = (sv3.get("t") or "").split(".")
    rrr = _safe_float(_get(t_parts, 1))

    return LiveScore(
        match_id=match_id,
        status_text=_STATUS_MAP.get(sv3.get("fsr", ""), sv3.get("fsr", "")),
        batting_team="",
        bowling_team="",
        current_score=sv3.get("ats") or "",
        current_overs=str(sv3.get("a") or ""),
        run_rate=_safe_float(sv3.get("q")),
        required_run_rate=rrr,
        last_5_overs=" | ".join(over_summaries),
        current_partnership="",
        batters_on_crease=batters,
        current_bowler=bowler,
        recent_balls=recent_balls,
    )


# ─────────────────────────────────────────────────────────────────────────────
# getSC4 helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_batting_str(raw: str, key_map: dict[str, str]) -> Optional[BattingEntry]:
    """'YT.61.42.0.0' → BattingEntry(player, runs, balls, fours, sixes)"""
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
    Format: 'playerKey.runs.balls.4s.6s.score1.score2.wkts.b1.b2/sr-econ/'
    Shorter entries like 'playerKey/sr-econ' or 'playerKey' are handled gracefully.
    """
    if not raw or not raw.strip():
        return None

    # Extract /sr-econ/ suffix if present
    sr, econ = None, None
    slash_m = re.search(r"/([0-9.]+)-([0-9.]+)/", raw)
    if slash_m:
        sr   = _safe_float(slash_m.group(1))
        econ = _safe_float(slash_m.group(2))

    # Main part before first slash
    main = raw.split("/")[0].strip()
    parts = main.split(".")
    if not parts or not parts[0]:
        return None

    player_key = parts[0]

    # balls faced → convert to overs (6 balls = 1 over)
    balls_raw = _safe_int(_get(parts, 2))
    overs = round(balls_raw / 6, 1) if balls_raw is not None else None

    return BowlingEntry(
        player=_resolve(player_key, key_map),
        runs=_safe_int(_get(parts, 1)),
        overs=overs,
        wickets=_safe_int(_get(parts, 7)),
        economy=econ,
    )


def _parse_extras_str(raw: str) -> Extras:
    """'0.1.5.0.0' → Extras(wides, no_balls, byes, leg_byes, penalty)"""
    parts = (raw or "0.0.0.0.0").split(".")
    vals = [_safe_int(_get(parts, i)) or 0 for i in range(5)]
    return Extras(
        wides=vals[0], no_balls=vals[1], byes=vals[2],
        leg_byes=vals[3], penalty=vals[4],
        total=sum(vals),
    )


def _parse_sc4_innings(raw: dict, inn_num: int, key_map: dict[str, str]) -> Innings:
    batting = [
        e for e in (_parse_batting_str(s, key_map) for s in (raw.get("a") or []))
        if e is not None
    ]
    bowling = [
        e for e in (_parse_bowling_str(s, key_map) for s in (raw.get("b") or []))
        if e is not None
    ]

    total_runs, total_overs = _decode_score(raw.get("d") or "")

    # Convert overs from balls: "305/8(300" → 300 balls = 50.0 overs
    overs_balls = _safe_int(total_overs)
    if overs_balls and overs_balls > 100:
        total_overs = str(round(overs_balls / 6, 1))

    extras = _parse_extras_str(raw.get("e") or "")

    return Innings(
        innings_number=_safe_int(raw.get("st")) or inn_num,
        batting_team="",
        bowling_team="",
        total=total_runs,
        overs=total_overs,
        extras=extras,
        batting=batting,
        bowling=bowling,
        is_super_over=False,
    )


def _parse_sc4(sc4: list, key_map: dict[str, str], match_id: str) -> Scorecard:
    innings_list = [
        _parse_sc4_innings(inn, i + 1, key_map)
        for i, inn in enumerate(sc4)
        if isinstance(inn, dict)
    ]
    return Scorecard(match_id=match_id, innings=innings_list, is_partial=False)


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
                is_boundary=(runs == 4),
                is_six=(runs == 6),
                commentary=str(item.get("c1") or ""),
            ))
        elif t == "o":
            balls.append(Ball(
                over=str(item.get("o") or ""),
                runs=_safe_int(item.get("runs")) or 0,
                is_wicket=False,
                is_boundary=False,
                is_six=False,
                commentary=(
                    f"End of over {item.get('o')}: "
                    f"{item.get('team', '')} {item.get('s', '')} — "
                    f"{item.get('bowler', '')} {item.get('rb', '')}"
                ),
            ))
    return balls


# ─────────────────────────────────────────────────────────────────────────────
# DOM fallbacks
# ─────────────────────────────────────────────────────────────────────────────

async def _parse_info_dom(page: Page, match_id: str) -> MatchInfo:
    log.debug("[{}] DOM fallback for Match Info", match_id)

    async def row(label: str) -> str:
        rows = await page.query_selector_all("[class*='info-row'], [class*='detail-row'], tr")
        for r in rows:
            if label.lower() in (await r.inner_text()).lower():
                cells = await r.query_selector_all("td, [class*='value']")
                if len(cells) >= 2:
                    return (await cells[-1].inner_text()).strip()
        return ""

    return MatchInfo(
        match_id=match_id,
        series=await _text(page, "[class*='series-name'], [class*='tournament']"),
        venue=await row("venue") or None,
        toss=await row("toss") or None,
        umpires=await _texts(page, "[class*='umpire']"),
        result=await row("result") or None,
    )


async def _parse_squads_dom(page: Page, match_id: str) -> Squads:
    log.debug("[{}] DOM fallback for Squads", match_id)
    teams: list[TeamSquad] = []
    sections = await page.query_selector_all(
        "[class*='team-squad'], [class*='playing-xi'], [class*='squad-list'], [class*='team-players']"
    )
    for section in sections:
        name_el = await section.query_selector("[class*='team-name'], h3, h4")
        team_name = (await name_el.inner_text()).strip() if name_el else "Unknown"
        player_els = await section.query_selector_all(
            "[class*='player-name'], [class*='player-item'] span"
        )
        players = [
            Player(name=(await el.inner_text()).strip())
            for el in player_els
            if (await el.inner_text()).strip()
        ]
        teams.append(TeamSquad(team_name=team_name, players=players))
    return Squads(match_id=match_id, teams=teams, announced=bool(teams))


async def _parse_scorecard_dom(page: Page, match_id: str) -> Scorecard:
    log.debug("[{}] DOM fallback for Scorecard", match_id)
    innings_list: list[Innings] = []
    sections = await page.query_selector_all(
        "[class*='team-inning'], [class*='scorecard-table'], [class*='innings-container']"
    )
    for idx, section in enumerate(sections, start=1):
        bt_el = await section.query_selector("[class*='team-name'], [class*='batting-team']")
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
    return Scorecard(match_id=match_id, innings=innings_list, is_partial=True)


async def _parse_live_dom(page: Page, match_id: str) -> LiveScore:
    log.debug("[{}] DOM fallback for Live", match_id)
    status = await _text(page, "[class*='live-score-header'], [class*='match-status']")
    score  = await _text(page, "[class*='live-score-card'], [class*='team-score']")
    overs  = await _text(page, "[class*='over-data'], [class*='overs']")
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

        sv3 = interceptor.find("getSV3")
        if sv3 and isinstance(sv3, dict) and sv3.get("mn"):
            return _parse_sv3_info(sv3, match_id)

        return await _parse_info_dom(page, match_id)


@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_squads(match_id: str, slug: str) -> Squads:
    url = f"{config.MATCH_DETAIL_BASE}/{slug}/match-details"
    log.info("[{}] Scraping Squads → {}", match_id, url)

    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await _navigate(page, url)
        await asyncio.sleep(random.uniform(config.MIN_REQUEST_DELAY, config.MAX_REQUEST_DELAY))
        # Squads are only in DOM (no squad list in getSV3)
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

        sc4   = interceptor.find("getSC4")
        feeds = interceptor.find("getBallFeeds") or []
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

        sv3   = interceptor.find("getSV3")
        feeds = interceptor.find("getBallFeeds") or []
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
    """Scrape Match Info and Squads concurrently (called on first discovery)."""
    info_task   = asyncio.create_task(scrape_match_info(match_id, slug))
    squads_task = asyncio.create_task(scrape_squads(match_id, slug))
    info, squads = await asyncio.gather(info_task, squads_task, return_exceptions=True)
    return {
        "match_info": info   if not isinstance(info,   Exception) else None,
        "squads":     squads if not isinstance(squads, Exception)  else None,
    }
