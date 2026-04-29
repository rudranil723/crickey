"""
scraper/match_detail.py
-----------------------
Scrapes all four tabs for a single match:
  • Match Info  (/match-details)
  • Squads      (/match-details  — Playing XI section)
  • Live        (/ default — Summary/Commentary)
  • Scorecard   (/match-scorecard)

Each tab has:
  1. An API-interception fast path (parses raw JSON from CREX's internal API).
  2. A DOM fallback path (parses the rendered HTML).

All parsers are defensive — missing fields produce None/empty lists rather
than exceptions, ensuring partial data is always persisted.
"""

from __future__ import annotations

import asyncio
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
# Internal helpers
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
        results = []
        for el in els:
            t = (await el.inner_text()).strip()
            if t:
                results.append(t)
        return results
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Match Info
# ─────────────────────────────────────────────────────────────────────────────

def _parse_info_api(data: Any, match_id: str) -> Optional[MatchInfo]:
    if not isinstance(data, dict):
        return None
    try:
        info = data.get("matchInfo") or data.get("info") or data.get("data") or data
        if not isinstance(info, dict):
            return None

        venue_obj = info.get("venue") or {}
        if isinstance(venue_obj, str):
            venue_name, city = venue_obj, None
        else:
            venue_name = venue_obj.get("name") or venue_obj.get("stadium")
            city = venue_obj.get("city") or venue_obj.get("location")

        time_raw = (
            info.get("startTime") or info.get("startDateTime") or
            info.get("matchTime") or info.get("date") or ""
        )

        toss = info.get("toss") or info.get("tossResult")
        if isinstance(toss, dict):
            toss = toss.get("result") or toss.get("text")

        umpires_raw = info.get("umpires") or []
        if isinstance(umpires_raw, str):
            umpires = [u.strip() for u in umpires_raw.split(",") if u.strip()]
        elif isinstance(umpires_raw, list):
            umpires = [
                (u.get("name") if isinstance(u, dict) else str(u))
                for u in umpires_raw
            ]
        else:
            umpires = []

        return MatchInfo(
            match_id=match_id,
            series=str(info.get("series", {}).get("name") or info.get("seriesName") or ""),
            match_number=str(info.get("matchNumber") or info.get("matchDesc") or ""),
            venue=str(venue_name) if venue_name else None,
            city=str(city) if city else None,
            date=str(info.get("dateStr") or info.get("dateDisplay") or ""),
            start_time_utc=parse_crex_datetime(str(time_raw)) if time_raw else None,
            toss=str(toss) if toss else None,
            umpires=umpires,
            match_referee=str(info.get("referee") or info.get("matchReferee") or "") or None,
            result=str(info.get("result") or info.get("status") or "") or None,
            player_of_match=str(info.get("playerOfMatch") or info.get("potm") or "") or None,
        )
    except Exception as exc:
        log.debug("Info API parse error: {}", exc)
        return None


async def _parse_info_dom(page: Page, match_id: str) -> MatchInfo:
    log.debug("[{}] DOM fallback for Match Info", match_id)

    async def row(label: str) -> str:
        """Find a table/detail row matching a label and return its value."""
        rows = await page.query_selector_all("[class*='info-row'], [class*='detail-row'], tr")
        for r in rows:
            text = (await r.inner_text()).lower()
            if label.lower() in text:
                cells = await r.query_selector_all("td, [class*='value'], [class*='detail-value']")
                if len(cells) >= 2:
                    return (await cells[-1].inner_text()).strip()
        return ""

    series   = await _text(page, "[class*='series-name'], [class*='tournament']")
    venue    = await row("venue")
    toss     = await row("toss")
    result   = await row("result")
    umpires  = await _texts(page, "[class*='umpire']")

    return MatchInfo(
        match_id=match_id,
        series=series,
        venue=venue or None,
        toss=toss or None,
        umpires=umpires,
        result=result or None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Squads
# ─────────────────────────────────────────────────────────────────────────────

def _parse_player(raw: Any) -> Player:
    if isinstance(raw, str):
        return Player(name=raw)
    if isinstance(raw, dict):
        name = raw.get("name") or raw.get("playerName") or raw.get("fullName") or "Unknown"
        role = raw.get("role") or raw.get("playerRole") or raw.get("specialization")
        is_captain = bool(raw.get("isCaptain") or raw.get("captain"))
        is_keeper  = bool(raw.get("isKeeper") or raw.get("keeper") or raw.get("isWk"))
        batting_order = _safe_int(raw.get("battingOrder") or raw.get("battingPosition"))
        return Player(
            name=str(name),
            role=str(role) if role else None,
            is_captain=is_captain,
            is_keeper=is_keeper,
            batting_order=batting_order,
        )
    return Player(name=str(raw))


def _parse_squads_api(data: Any, match_id: str) -> Optional[Squads]:
    if not isinstance(data, dict):
        return None
    try:
        raw_squads = (
            data.get("squads") or data.get("playing11") or
            data.get("teams") or data.get("data") or []
        )
        # Sometimes wrapped: {"data": {"squads": [...]}}
        if isinstance(raw_squads, dict):
            raw_squads = (
                raw_squads.get("squads") or raw_squads.get("playing11") or []
            )
        if not isinstance(raw_squads, list) or not raw_squads:
            return None

        teams: list[TeamSquad] = []
        for team_raw in raw_squads:
            if not isinstance(team_raw, dict):
                continue
            team_name = (
                team_raw.get("teamName") or team_raw.get("name") or
                team_raw.get("team", {}).get("name") or "Unknown"
            )
            players_raw = (
                team_raw.get("players") or team_raw.get("playing11") or
                team_raw.get("squad") or []
            )
            bench_raw = team_raw.get("bench") or team_raw.get("reserves") or []

            teams.append(TeamSquad(
                team_name=str(team_name),
                players=[_parse_player(p) for p in players_raw],
                bench=[_parse_player(p) for p in bench_raw],
            ))

        return Squads(match_id=match_id, teams=teams, announced=bool(teams))
    except Exception as exc:
        log.debug("Squads API parse error: {}", exc)
        return None


async def _parse_squads_dom(page: Page, match_id: str) -> Squads:
    log.debug("[{}] DOM fallback for Squads", match_id)
    teams: list[TeamSquad] = []

    team_sections = await page.query_selector_all(
        "[class*='team-squad'], [class*='playing-xi'], [class*='squad-list']"
    )
    if not team_sections:
        team_sections = await page.query_selector_all("[class*='team-players']")

    for section in team_sections:
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


# ─────────────────────────────────────────────────────────────────────────────
# Scorecard
# ─────────────────────────────────────────────────────────────────────────────

def _parse_batting_entry(raw: dict) -> BattingEntry:
    return BattingEntry(
        player=str(raw.get("name") or raw.get("batsman") or raw.get("playerName") or ""),
        dismissal=str(raw.get("dismissal") or raw.get("howOut") or raw.get("wicketInfo") or ""),
        runs=_safe_int(raw.get("runs") or raw.get("r")),
        balls=_safe_int(raw.get("balls") or raw.get("b")),
        fours=_safe_int(raw.get("fours") or raw.get("4s")),
        sixes=_safe_int(raw.get("sixes") or raw.get("6s")),
        strike_rate=_safe_float(raw.get("strikeRate") or raw.get("sr")),
    )


def _parse_bowling_entry(raw: dict) -> BowlingEntry:
    return BowlingEntry(
        player=str(raw.get("name") or raw.get("bowler") or raw.get("playerName") or ""),
        overs=_safe_float(raw.get("overs") or raw.get("o")),
        maidens=_safe_int(raw.get("maidens") or raw.get("m")),
        runs=_safe_int(raw.get("runs") or raw.get("r")),
        wickets=_safe_int(raw.get("wickets") or raw.get("w")),
        no_balls=_safe_int(raw.get("noBalls") or raw.get("nb")),
        wides=_safe_int(raw.get("wides") or raw.get("wd")),
        economy=_safe_float(raw.get("economy") or raw.get("econ") or raw.get("er")),
    )


def _parse_extras(raw: Any) -> Extras:
    if not isinstance(raw, dict):
        return Extras()
    return Extras(
        wides=_safe_int(raw.get("wides") or raw.get("w") or raw.get("wide")) or 0,
        no_balls=_safe_int(raw.get("noBalls") or raw.get("nb")) or 0,
        byes=_safe_int(raw.get("byes") or raw.get("b")) or 0,
        leg_byes=_safe_int(raw.get("legByes") or raw.get("lb")) or 0,
        penalty=_safe_int(raw.get("penalty") or raw.get("pen")) or 0,
        total=_safe_int(raw.get("total") or raw.get("totalExtras")) or 0,
    )


def _parse_innings_api(raw: dict, innings_number: int) -> Innings:
    batting_raw = raw.get("batting") or raw.get("batsmen") or raw.get("battingData") or []
    bowling_raw = raw.get("bowling") or raw.get("bowlers") or raw.get("bowlingData") or []
    fow_raw     = raw.get("fallOfWickets") or raw.get("fow") or []

    fow: list[FallOfWicket] = []
    for i, w in enumerate(fow_raw, start=1):
        if isinstance(w, dict):
            fow.append(FallOfWicket(
                wicket_number=i,
                runs_at_fall=_safe_int(w.get("runs") or w.get("score")) or 0,
                player=str(w.get("player") or w.get("batsman") or ""),
                overs_at_fall=_safe_float(w.get("overs") or w.get("over")),
            ))

    is_super = bool(raw.get("isSuperOver") or raw.get("superOver"))
    return Innings(
        innings_number=innings_number,
        batting_team=str(raw.get("battingTeam") or raw.get("team") or raw.get("teamName") or ""),
        bowling_team=str(raw.get("bowlingTeam") or raw.get("oppositeTeam") or ""),
        total=str(raw.get("total") or raw.get("score") or raw.get("runs") or ""),
        overs=str(raw.get("overs") or ""),
        run_rate=_safe_float(raw.get("runRate") or raw.get("rr") or raw.get("crr")),
        target=_safe_int(raw.get("target")),
        dls_target=_safe_int(raw.get("dlsTarget") or raw.get("revisedTarget")),
        batting=[_parse_batting_entry(b) for b in batting_raw if isinstance(b, dict)],
        bowling=[_parse_bowling_entry(b) for b in bowling_raw if isinstance(b, dict)],
        extras=_parse_extras(raw.get("extras")),
        fall_of_wickets=fow,
        is_super_over=is_super,
    )


# ── Scorecard keyword sets (lowercase for fast matching) ─────────────────────
_SC_URL_KEYWORDS = frozenset((
    "getsc", "getscore", "scorecard", "innings", "batting",
    "getmatchsc", "getbatting", "inningsdata",
))
_LIVE_URL_KEYWORDS = frozenset((
    "getlive", "livescore", "getls", "commentary", "livematch",
    "getsv", "getlm", "getlivedata", "livedata",
))


def _url_matches(captured_url: str, keywords: frozenset) -> bool:
    low = captured_url.lower()
    return any(kw in low for kw in keywords)


def _parse_scorecard_api(data: Any, match_id: str) -> Optional[Scorecard]:
    if not isinstance(data, dict):
        return None
    try:
        innings_raw = (
            data.get("innings") or data.get("scorecard") or
            data.get("data", {}).get("innings") or []
        )
        if not isinstance(innings_raw, list):
            return None

        innings_list = [
            _parse_innings_api(inn, i + 1)
            for i, inn in enumerate(innings_raw)
            if isinstance(inn, dict)
        ]
        return Scorecard(match_id=match_id, innings=innings_list, is_partial=True)
    except Exception as exc:
        log.debug("Scorecard API parse error: {}", exc)
        return None


async def _parse_scorecard_dom(page: Page, match_id: str) -> Scorecard:
    """DOM fallback: parse scorecard tables from the rendered page."""
    log.debug("[{}] DOM fallback for Scorecard", match_id)
    innings_list: list[Innings] = []
    innings_number = 0

    # Broad selector set — CREX uses different class names across match types
    inning_sections = await page.query_selector_all(
        "[class*='team-inning'], [class*='scorecard-table'], "
        "[class*='innings-container'], [class*='innings-wrapper'], "
        "[class*='inning-section'], [class*='score-wrapper']"
    )

    for section in inning_sections:
        innings_number += 1
        batting_team_el = await section.query_selector(
            "[class*='team-name'], [class*='batting-team'], [class*='inning-title']"
        )
        batting_team = (await batting_team_el.inner_text()).strip() if batting_team_el else ""

        # ── Batting rows ──────────────────────────────────────────────────────
        batting_rows = await section.query_selector_all(
            "[class*='player-data'], [class*='batting-row'], "
            "[class*='batsman-row'], [class*='batter-row'], tbody tr"
        )
        batting: list[BattingEntry] = []
        for row in batting_rows:
            cells = await row.query_selector_all("td")
            if not cells:
                cells = await row.query_selector_all("[class*='cell'], [class*='col']")
            texts = [(await c.inner_text()).strip() for c in cells]
            # CREX scorecard: Player | Dismissal | R | B | 4s | 6s | SR
            if len(texts) >= 5 and texts[0] and not texts[0].lower().startswith("total"):
                batting.append(BattingEntry(
                    player=texts[0],
                    dismissal=texts[1] if len(texts) > 1 else None,
                    runs=_safe_int(texts[2]) if len(texts) > 2 else None,
                    balls=_safe_int(texts[3]) if len(texts) > 3 else None,
                    fours=_safe_int(texts[4]) if len(texts) > 4 else None,
                    sixes=_safe_int(texts[5]) if len(texts) > 5 else None,
                    strike_rate=_safe_float(texts[6]) if len(texts) > 6 else None,
                ))

        # ── Bowling rows ──────────────────────────────────────────────────────
        bowling_rows = await section.query_selector_all(
            "[class*='bowler-table'] tr, [class*='bowling-row'], "
            "[class*='bowler-row'], [class*='bowl-row']"
        )
        bowling: list[BowlingEntry] = []
        for row in bowling_rows:
            cells = await row.query_selector_all("td")
            if not cells:
                cells = await row.query_selector_all("[class*='cell'], [class*='col']")
            texts = [(await c.inner_text()).strip() for c in cells]
            if len(texts) >= 4 and texts[0]:
                bowling.append(BowlingEntry(
                    player=texts[0],
                    overs=_safe_float(texts[1]) if len(texts) > 1 else None,
                    maidens=_safe_int(texts[2]) if len(texts) > 2 else None,
                    runs=_safe_int(texts[3]) if len(texts) > 3 else None,
                    wickets=_safe_int(texts[4]) if len(texts) > 4 else None,
                    economy=_safe_float(texts[5]) if len(texts) > 5 else None,
                ))

        if batting or bowling:
            innings_list.append(Innings(
                innings_number=innings_number,
                batting_team=batting_team,
                bowling_team="",
                batting=batting,
                bowling=bowling,
            ))

    return Scorecard(match_id=match_id, innings=innings_list, is_partial=True)


# ─────────────────────────────────────────────────────────────────────────────
# Live / Commentary
# ─────────────────────────────────────────────────────────────────────────────

def _parse_live_api(data: Any, match_id: str) -> Optional[LiveScore]:
    if not isinstance(data, dict):
        return None
    try:
        live = data.get("live") or data.get("liveScore") or data.get("data") or data
        if not isinstance(live, dict):
            return None

        # Recent balls from commentary
        commentary_raw = (
            data.get("commentary") or data.get("recentCommentary") or
            live.get("commentary") or []
        )
        balls: list[Ball] = []
        for c in commentary_raw[:20]:
            if not isinstance(c, dict):
                continue
            balls.append(Ball(
                over=str(c.get("over") or c.get("overNo") or ""),
                runs=_safe_int(c.get("runs") or c.get("score")) or 0,
                is_wicket=bool(c.get("isWicket") or c.get("wicket")),
                is_boundary=bool(c.get("isBoundary") or c.get("boundary")),
                is_six=bool(c.get("isSix") or c.get("six")),
                extras=str(c.get("extras") or ""),
                commentary=str(c.get("commentary") or c.get("text") or c.get("description") or ""),
            ))

        # Batters on crease
        batters_raw = live.get("batsmen") or live.get("currentBatsmen") or []
        batters = [_parse_batting_entry(b) for b in batters_raw if isinstance(b, dict)]

        # Current bowler
        bowler_raw = live.get("currentBowler") or live.get("bowler")
        bowler = _parse_bowling_entry(bowler_raw) if isinstance(bowler_raw, dict) else None

        return LiveScore(
            match_id=match_id,
            status_text=str(live.get("statusText") or live.get("matchStatus") or live.get("status") or ""),
            batting_team=str(live.get("battingTeam") or live.get("currentBattingTeam") or ""),
            bowling_team=str(live.get("bowlingTeam") or live.get("currentBowlingTeam") or ""),
            current_score=str(live.get("score") or live.get("currentScore") or ""),
            current_overs=str(live.get("overs") or live.get("currentOvers") or ""),
            run_rate=_safe_float(live.get("runRate") or live.get("crr")),
            required_run_rate=_safe_float(live.get("requiredRunRate") or live.get("rrr")),
            last_5_overs=str(live.get("last5Overs") or live.get("recentOvers") or ""),
            current_partnership=str(live.get("partnership") or live.get("currentPartnership") or ""),
            batters_on_crease=batters,
            current_bowler=bowler,
            recent_balls=balls,
        )
    except Exception as exc:
        log.debug("Live API parse error: {}", exc)
        return None


async def _parse_live_dom(page: Page, match_id: str) -> LiveScore:
    log.debug("[{}] DOM fallback for Live", match_id)
    status   = await _text(page, "[class*='live-score-header'], [class*='match-status'], [class*='status-text']")
    score    = await _text(page, "[class*='live-score-card'], [class*='team-score'], [class*='current-score']")
    overs    = await _text(page, "[class*='over-data'], [class*='overs'], [class*='over-count']")

    commentary_els = await page.query_selector_all(
        "[class*='cm-b-ballupdate'], [class*='commentary-item'], [class*='ball-item']"
    )
    balls: list[Ball] = []
    for el in commentary_els[:20]:
        text = (await el.inner_text()).strip()
        if text:
            balls.append(Ball(over="", runs=0, commentary=text))

    return LiveScore(
        match_id=match_id,
        status_text=status,
        batting_team="",
        bowling_team="",
        current_score=score or None,
        current_overs=overs or None,
        recent_balls=balls,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public orchestrator
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

        for captured in interceptor.captured:
            result = _parse_info_api(captured["data"], match_id)
            if result:
                return result

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

        for captured in interceptor.captured:
            result = _parse_squads_api(captured["data"], match_id)
            if result and result.announced:
                return result

        return await _parse_squads_dom(page, match_id)


@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_scorecard(match_id: str, slug: str) -> Scorecard:
    url = f"{config.MATCH_DETAIL_BASE}/{slug}/match-scorecard"
    log.info("[{}] Scraping Scorecard → {}", match_id, url)

    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await _navigate(page, url)

        # Wider selector — scorecard renders more slowly than static tabs
        try:
            await page.wait_for_selector(
                "table, [class*='scorecard'], [class*='team-inning'], "
                "[class*='innings-container'], [class*='score-wrapper'], "
                "[class*='batting-row']",
                timeout=9000,
            )
        except PlaywrightTimeout:
            log.warning("[{}] Scorecard selector timed out — proceeding with intercepted data", match_id)

        await asyncio.sleep(random.uniform(config.MIN_REQUEST_DELAY, config.MAX_REQUEST_DELAY))

        # ── Fast path: URL-keyword filtered ──────────────────────────────────
        for captured in interceptor.captured:
            if _url_matches(captured.get("url", ""), _SC_URL_KEYWORDS):
                result = _parse_scorecard_api(captured["data"], match_id)
                if result and result.innings:
                    log.info("[{}] Scorecard via API ({} innings) from {}",
                             match_id, len(result.innings), captured["url"])
                    return result

        # ── Fast path: unfiltered fallback ────────────────────────────────────
        for captured in interceptor.captured:
            result = _parse_scorecard_api(captured["data"], match_id)
            if result and result.innings:
                log.info("[{}] Scorecard via API (unfiltered, {} innings)",
                         match_id, len(result.innings))
                return result

        # ── DOM fallback ──────────────────────────────────────────────────────
        log.debug("[{}] Scorecard falling back to DOM", match_id)
        return await _parse_scorecard_dom(page, match_id)


@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def scrape_live(match_id: str, slug: str) -> LiveScore:
    """Try root URL first, then explicit /live-score path."""
    urls_to_try = [
        f"{config.MATCH_DETAIL_BASE}/{slug}",
        f"{config.MATCH_DETAIL_BASE}/{slug}/live-score",
    ]
    log.info("[{}] Scraping Live score", match_id)

    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()

        for url in urls_to_try:
            await _navigate(page, url)
            try:
                await page.wait_for_selector(
                    "[class*='live'], [class*='score'], "
                    "[class*='commentary'], [class*='ball'], "
                    "[class*='current-score']",
                    timeout=6000,
                )
            except PlaywrightTimeout:
                pass

            # Live endpoints finish populating after networkidle — extra wait
            await asyncio.sleep(random.uniform(2.5, 4.0))

            # ── Fast path: URL-keyword filtered ──────────────────────────────
            for captured in interceptor.captured:
                if _url_matches(captured.get("url", ""), _LIVE_URL_KEYWORDS):
                    result = _parse_live_api(captured["data"], match_id)
                    if result and result.current_score:
                        log.info("[{}] Live score via API: {}", match_id, result.current_score)
                        return result

            # ── Fast path: unfiltered fallback ────────────────────────────────
            for captured in interceptor.captured:
                result = _parse_live_api(captured["data"], match_id)
                if result and result.current_score:
                    log.info("[{}] Live score via API (unfiltered): {}", match_id, result.current_score)
                    return result

        # ── DOM fallback ──────────────────────────────────────────────────────
        log.debug("[{}] Live falling back to DOM", match_id)
        return await _parse_live_dom(page, match_id)


async def scrape_all_static(match_id: str, slug: str) -> dict:
    """
    Scrape Match Info and Squads concurrently.
    Called once when a match is first discovered.
    Returns a dict with both results.
    """
    info_task   = asyncio.create_task(scrape_match_info(match_id, slug))
    squads_task = asyncio.create_task(scrape_squads(match_id, slug))

    info, squads = await asyncio.gather(info_task, squads_task, return_exceptions=True)

    return {
        "match_info": info if not isinstance(info, Exception) else None,
        "squads":     squads if not isinstance(squads, Exception) else None,
    }
