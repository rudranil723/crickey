"""
scraper/match_list.py
---------------------
Scrapes the CREX fixture / match-list page to produce a list of
MatchSummary objects representing all upcoming, live, and recent matches.

Strategy (hybrid)
-----------------
1. Navigate to /fixtures/match-list with an APIInterceptor attached.
2. If the interceptor captures a JSON response containing match arrays,
   parse that (fast path — no HTML involved).
3. Otherwise fall back to DOM parsing of .match-card-wrapper elements.

Both paths normalise data into the same MatchSummary schema.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import timezone
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

import config
from models import MatchSummary
from scraper.browser import pool
from scraper.interceptor import APIInterceptor
from utils.logger import log
from utils.retry import retry
from utils.time_utils import parse_crex_datetime, utc_now


# ── Helpers ───────────────────────────────────────────────────────────────────

# Matches strings that are purely score-like: digits, slashes, parens, dots, spaces, colons, semicolons
_SCORE_RE = re.compile(r'[\d/\(\)\.\s;:C]+$')
# A score must have mostly numbers/symbols. We allow 'W', 'C', 'D' but not all of A-Z
_SCORE_ONLY_RE = re.compile(r'^[\d\s/\(\)\.\-:;WCD]+$', re.IGNORECASE)


def _clean_team_name(raw: str | None) -> str | None:
    """Strip score artifacts from team names; return None if value looks like a score."""
    if not raw:
        return None
    # If the text has multiple lines, the first line is always the team name
    cleaned = raw.strip().split('\n')[0].strip()
    if not cleaned:
        return None
    # If the entire string looks like a score/number, discard it
    if _SCORE_ONLY_RE.fullmatch(cleaned):
        return None
    # Strip trailing score-like suffixes (e.g. "India 245/6")
    cleaned = _SCORE_RE.sub('', cleaned).strip()
    return cleaned or None


def _extract_match_id(url: str) -> str:
    """Pull the last path segment as match id, e.g. 'ind-vs-aus-abc123' → 'abc123'."""
    parts = [p for p in url.rstrip("/").split("/") if p]
    slug = parts[-1] if parts else "unknown"
    # Match IDs on CREX are alphanumeric suffixes after the last hyphen group
    m = re.search(r"([a-zA-Z0-9]{6,})$", slug)
    return m.group(1) if m else slug


def _build_detail_url(slug: str) -> str:
    return f"{config.MATCH_DETAIL_BASE}/{slug}"


def infer_match_status(card_text: str, score_text: str, result_text: str, match_id: str = "unknown") -> str:
    """
    Infer if a match is live, completed, or upcoming using multiple signals.
    """
    try:
        # Combine texts for a broad keyword search
        combined_text = f"{card_text} {score_text} {result_text}".lower()

        # 1. Strong "completed" signals
        if any(k in combined_text for k in ("won by", "drawn", "tied", "abandoned", "no result", "match over", "result")):
            log.debug("infer_match_status [{}]: 'completed' inferred from result keywords.", match_id)
            return "completed"

        # 2. Strong "live" signals
        if any(k in combined_text for k in ("live", "in progress", "batting", "bowling", "stumps", "lunch", "tea", "rain delay", "opted to", "chose to")):
            log.debug("infer_match_status [{}]: 'live' inferred from live keywords.", match_id)
            return "live"

        # 3. Score-based live signal (e.g., "155/7", "23-0")
        if score_text and any(c.isdigit() for c in score_text) and any(c in score_text for c in ['/', '-']):
            log.debug("infer_match_status [{}]: 'live' inferred from presence of score: '{}'", match_id, score_text)
            return "live"

        # 4. Fallback completed (e.g., "won")
        if "won" in combined_text or "win" in combined_text:
            log.debug("infer_match_status [{}]: 'completed' inferred from 'won/win'.", match_id)
            return "completed"

    except Exception as e:
        log.error("infer_match_status [{}]: Exception during inference: {}. Defaulting to 'upcoming'.", match_id, e)
        return "upcoming"

    log.debug("infer_match_status [{}]: No strong signals found, defaulting to 'upcoming'.", match_id)
    return "upcoming"


# ── API-path parser ───────────────────────────────────────────────────────────

def _parse_api_response(data: dict | list) -> list[MatchSummary]:
    """
    Attempt to parse a captured API JSON payload.
    CREX API responses can be wrapped in various envelope shapes;
    we try the most common ones.
    """
    matches_raw: list[dict] = []

    if isinstance(data, list):
        matches_raw = data
    elif isinstance(data, dict):
        for key in ("data", "matches", "fixtures", "result", "items", "matchList"):
            val = data.get(key)
            if isinstance(val, list):
                matches_raw = val
                break
            if isinstance(val, dict):
                # one level deeper
                for k2 in ("matches", "fixtures", "data"):
                    inner = val.get(k2)
                    if isinstance(inner, list):
                        matches_raw = inner
                        break
                if matches_raw:
                    break

    summaries: list[MatchSummary] = []
    for raw in matches_raw:
        if not isinstance(raw, dict):
            continue
        try:
            slug = (
                raw.get("slug") or
                raw.get("matchSlug") or
                raw.get("url") or
                raw.get("matchId") or
                "unknown"
            )
            match_id = raw.get("matchId") or raw.get("id") or _extract_match_id(str(slug))
            team_a = (
                raw.get("teamA", {}).get("name") or
                raw.get("team1", {}).get("name") or
                raw.get("homeTeam") or
                "TBD"
            )
            team_b = (
                raw.get("teamB", {}).get("name") or
                raw.get("team2", {}).get("name") or
                raw.get("awayTeam") or
                "TBD"
            )
            series = (
                raw.get("seriesName") or
                raw.get("series", {}).get("name") or
                raw.get("competition") or
                ""
            )
            match_type = raw.get("matchType") or raw.get("format") or "Other"
            venue = (
                raw.get("venue") or
                raw.get("ground") or
                raw.get("stadium")
            )
            status_raw = str(
                raw.get("status") or
                raw.get("matchStatus") or
                raw.get("state") or
                ""
            )
            result_raw = str(
                raw.get("result") or
                raw.get("statusText") or
                ""
            )
            score_raw = str(
                raw.get("score") or
                raw.get("team1Score") or
                raw.get("team2Score") or
                ""
            )
            
            # Extract generic text from the whole raw object just in case
            card_text = f"{status_raw} {result_raw}"
            status = infer_match_status(card_text, score_raw, result_raw, str(match_id))

            # Parse time — try multiple field names
            time_raw = (
                raw.get("startTime") or
                raw.get("startDateTime") or
                raw.get("matchTime") or
                raw.get("date") or
                ""
            )
            start_time = parse_crex_datetime(str(time_raw)) if time_raw else None
            detail_url = _build_detail_url(str(slug))

            summaries.append(MatchSummary(
                match_id=str(match_id),
                match_slug=str(slug),
                detail_url=detail_url,
                team_a=str(team_a),
                team_b=str(team_b),
                series_name=str(series),
                match_type=str(match_type),
                venue=str(venue) if venue else None,
                start_time=start_time,
                status=status,
            ))
        except Exception as exc:
            log.warning("Skipping malformed match record: {} — {}", raw, exc)

    return summaries


# ── DOM-path parser ───────────────────────────────────────────────────────────

async def _parse_dom(page: Page) -> list[MatchSummary]:
    """
    Fallback: extract match data directly from the rendered DOM.
    Tries multiple selector strategies to be resilient against CSS class changes.
    """
    log.info("Falling back to DOM parsing for match list")
    summaries: list[MatchSummary] = []

    # Wait for at least one match card to appear
    card_selectors = [
        ".match-card-wrapper",
        ".match-card",
        "[class*='match-card']",
        "[class*='fixture']",
        "a[href*='cricket-live-score']",
    ]
    cards = []
    for sel in card_selectors:
        try:
            await page.wait_for_selector(sel, timeout=8_000)
            cards = await page.query_selector_all(sel)
            if cards:
                log.debug("DOM selector '{}' found {} cards", sel, len(cards))
                break
        except PlaywrightTimeout:
            continue

    if not cards:
        log.warning("No match cards found in DOM")
        return summaries

    for card in cards:
        try:
            href = await card.get_attribute("href") or ""
            if "cricket-live-score" not in href:
                # Try child anchor
                anchor = await card.query_selector("a[href*='cricket-live-score']")
                if anchor:
                    href = await anchor.get_attribute("href") or ""

            if not href:
                continue

            full_url = href if href.startswith("http") else f"{config.BASE_URL}{href}"
            slug = full_url.split("/cricket-live-score/")[-1].split("/")[0]
            match_id = _extract_match_id(slug)

            # Team names — use specific selectors to avoid picking up score elements
            team_els = await card.query_selector_all(
                "[class*='team-name'], [class*='teamName']"
            )
            teams = [await el.inner_text() for el in team_els]
            teams = [t.strip() for t in teams if t.strip()]

            team_a = _clean_team_name(teams[0]) or "TBD" if len(teams) > 0 else "TBD"
            team_b = _clean_team_name(teams[1]) or "TBD" if len(teams) > 1 else "TBD"

            # Status / Result texts
            status_el = await card.query_selector(
                "[class*='status'], [class*='result'], [class*='match-status'], [class*='win-text']"
            )
            result_raw = (await status_el.inner_text()).strip() if status_el else ""

            # Score text
            score_el = await card.query_selector(
                "[class*='score'], [class*='innings']"
            )
            score_raw = (await score_el.inner_text()).strip() if score_el else ""
            
            # Entire card text
            card_text = (await card.inner_text()).strip()

            status = infer_match_status(card_text, score_raw, result_raw, match_id)

            # Series / match type
            series_el = await card.query_selector(
                "[class*='series'], [class*='league'], [class*='competition']"
            )
            series = (await series_el.inner_text()).strip() if series_el else ""

            # Date/time
            time_el = await card.query_selector(
                "[class*='time'], [class*='date'], [class*='schedule']"
            )
            time_raw = (await time_el.inner_text()).strip() if time_el else ""
            start_time = parse_crex_datetime(time_raw) if time_raw else None

            summaries.append(MatchSummary(
                match_id=match_id,
                match_slug=slug,
                detail_url=full_url,
                team_a=team_a,
                team_b=team_b,
                series_name=series,
                match_type="Other",
                start_time=start_time,
                status=status,
            ))
        except Exception as exc:
            log.warning("DOM parse error for card: {}", exc)

    return summaries


# ── Public entrypoint ─────────────────────────────────────────────────────────

@retry(max_attempts=config.MAX_RETRIES, base_delay=config.RETRY_BASE_DELAY)
async def fetch_match_list() -> list[MatchSummary]:
    """
    Main entry point. Returns a deduplicated list of MatchSummary objects
    representing all matches currently visible on the fixture list page.
    """
    log.info("Fetching match list from {}", config.MATCH_LIST_URL)

    async with pool.page() as page:
        interceptor = APIInterceptor(page)
        await interceptor.attach()

        try:
            await page.goto(
                config.MATCH_LIST_URL,
                wait_until="networkidle",
                timeout=config.PAGE_TIMEOUT_MS,
            )
        except PlaywrightTimeout:
            log.warning("Page load timed out — attempting DOM parse with partial content")

        # ── Try API interception first ────────────────────────────────────────
        summaries: list[MatchSummary] = []
        for captured in interceptor.captured:
            parsed = _parse_api_response(captured["data"])
            if parsed:
                summaries.extend(parsed)
                log.info("API interception yielded {} matches from {}", len(parsed), captured["url"])

        # ── Fall back to DOM if no API data ───────────────────────────────────
        if not summaries:
            summaries = await _parse_dom(page)

        # Jitter to avoid hammering the server
        await asyncio.sleep(random.uniform(config.MIN_REQUEST_DELAY, config.MAX_REQUEST_DELAY))

    # Deduplicate by match_id (keep first occurrence)
    seen: set[str] = set()
    unique: list[MatchSummary] = []
    for s in summaries:
        if s.match_id not in seen:
            seen.add(s.match_id)
            unique.append(s)

    log.info("Match list: {} unique matches found", len(unique))
    return unique
