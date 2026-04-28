"""
scheduler.py
------------
Orchestrates all scraping jobs using APScheduler's AsyncIOScheduler.

Job Lifecycle
─────────────
1. POLLER_JOB  (every SCHEDULE_POLL_INTERVAL seconds)
   └── fetch_match_list()
       ├── New match discovered → schedule STATIC_JOB (info + squads)
       ├── Match going live soon (within PRE_WARM_SECONDS) →
       │     schedule LIVE_JOB + SCORECARD_JOB if not already running
       ├── Match live → ensure LIVE_JOB + SCORECARD_JOB running
       └── Match completed → cancel LIVE_JOB + SCORECARD_JOB, run final SCORECARD

2. STATIC_JOB  (one-shot per match)
   └── scrape_all_static() → saves match_info.json + squads.json

3. LIVE_JOB    (every LIVE_POLL_INTERVAL seconds while match is live)
   └── scrape_live()      → saves live/{ts}.json + live_latest.json

4. SCORECARD_JOB (every SCORECARD_POLL_INTERVAL seconds while match is live)
   └── scrape_scorecard() → saves scorecard/{ts}.json + scorecard_latest.json

5. FINAL_SCORECARD_JOB (one-shot after match ends)
   └── scrape_scorecard() with is_partial=False
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

import config
from models import MatchSummary
from scraper import match_detail
from scraper.match_list import fetch_match_list
from storage import (
    save_live, save_match_info,
    save_schedule, save_scorecard, save_squads,
)
from utils.logger import log
from utils.time_utils import utc_now


class MatchJobRegistry:
    """Tracks which APScheduler jobs exist for each match_id."""

    def __init__(self) -> None:
        self._jobs: dict[str, set[str]] = {}       # match_id → set[job_id]
        self._static_done: set[str] = set()        # match_ids with static data fetched
        self._completed: set[str] = set()          # match_ids that have ended
        self._known: set[str] = set()              # all seen match_ids

    def has_job(self, match_id: str, kind: str) -> bool:
        return kind in self._jobs.get(match_id, set())

    def register(self, match_id: str, kind: str) -> None:
        self._jobs.setdefault(match_id, set()).add(kind)

    def unregister(self, match_id: str, kind: str) -> None:
        self._jobs.get(match_id, set()).discard(kind)

    def is_static_done(self, match_id: str) -> bool:
        return match_id in self._static_done

    def mark_static_done(self, match_id: str) -> None:
        self._static_done.add(match_id)

    def is_completed(self, match_id: str) -> bool:
        return match_id in self._completed

    def mark_completed(self, match_id: str) -> None:
        self._completed.add(match_id)

    def is_known(self, match_id: str) -> bool:
        return match_id in self._known

    def mark_known(self, match_id: str) -> None:
        self._known.add(match_id)


# ── Module-level singletons ───────────────────────────────────────────────────
_scheduler = AsyncIOScheduler(timezone="UTC")
_registry  = MatchJobRegistry()


# ── Job functions ─────────────────────────────────────────────────────────────

async def _job_scrape_static(match_id: str, slug: str) -> None:
    log.info("[{}] Static scrape job started", match_id)
    try:
        results = await match_detail.scrape_all_static(match_id, slug)
        info   = results.get("match_info")
        squads = results.get("squads")
        if info:
            await save_match_info(match_id, info.model_dump(mode="json"))
        if squads:
            await save_squads(match_id, squads.model_dump(mode="json"))
        _registry.mark_static_done(match_id)
        log.info("[{}] Static scrape complete", match_id)
    except Exception as exc:
        log.error("[{}] Static scrape failed: {}", match_id, exc)


async def _job_scrape_live(match_id: str, slug: str) -> None:
    if _registry.is_completed(match_id):
        _cancel_job(match_id, "live")
        return
    try:
        live = await match_detail.scrape_live(match_id, slug)
        await save_live(match_id, live.model_dump(mode="json"))

        # Detect match end from status text
        status = live.status_text.lower()
        if any(k in status for k in ("won", "draw", "tie", "abandoned", "no result")):
            log.info("[{}] Match appears to have ended (status: '{}')", match_id, live.status_text)
            _handle_match_end(match_id, slug)
    except Exception as exc:
        log.error("[{}] Live poll failed: {}", match_id, exc)


async def _job_scrape_scorecard(match_id: str, slug: str) -> None:
    if _registry.is_completed(match_id):
        _cancel_job(match_id, "scorecard")
        return
    try:
        scorecard = await match_detail.scrape_scorecard(match_id, slug)
        await save_scorecard(match_id, scorecard.model_dump(mode="json"))
    except Exception as exc:
        log.error("[{}] Scorecard poll failed: {}", match_id, exc)


async def _job_final_scorecard(match_id: str, slug: str) -> None:
    log.info("[{}] Final scorecard scrape", match_id)
    try:
        scorecard = await match_detail.scrape_scorecard(match_id, slug)
        scorecard.is_partial = False
        await save_scorecard(match_id, scorecard.model_dump(mode="json"))
    except Exception as exc:
        log.error("[{}] Final scorecard failed: {}", match_id, exc)


# ── Scheduling helpers ────────────────────────────────────────────────────────

def _job_id(match_id: str, kind: str) -> str:
    return f"{kind}_{match_id}"


def _cancel_job(match_id: str, kind: str) -> None:
    jid = _job_id(match_id, kind)
    if _scheduler.get_job(jid):
        _scheduler.remove_job(jid)
        _registry.unregister(match_id, kind)
        log.debug("[{}] Cancelled {} job", match_id, kind)


def _schedule_static(match: MatchSummary) -> None:
    if _registry.is_static_done(match.match_id) or _registry.has_job(match.match_id, "static"):
        return
    jid = _job_id(match.match_id, "static")
    _scheduler.add_job(
        _job_scrape_static,
        trigger=DateTrigger(run_date=utc_now()),   # run immediately
        id=jid,
        args=[match.match_id, match.match_slug],
        replace_existing=True,
        misfire_grace_time=120,
    )
    _registry.register(match.match_id, "static")
    log.info("[{}] Static scrape job queued", match.match_id)


def _schedule_live_polling(match: MatchSummary) -> None:
    if not _registry.has_job(match.match_id, "live"):
        jid = _job_id(match.match_id, "live")
        _scheduler.add_job(
            _job_scrape_live,
            trigger=IntervalTrigger(seconds=config.LIVE_POLL_INTERVAL),
            id=jid,
            args=[match.match_id, match.match_slug],
            replace_existing=True,
            misfire_grace_time=config.LIVE_POLL_INTERVAL,
        )
        _registry.register(match.match_id, "live")
        log.info("[{}] Live polling started (every {}s)", match.match_id, config.LIVE_POLL_INTERVAL)

    if not _registry.has_job(match.match_id, "scorecard"):
        jid = _job_id(match.match_id, "scorecard")
        _scheduler.add_job(
            _job_scrape_scorecard,
            trigger=IntervalTrigger(seconds=config.SCORECARD_POLL_INTERVAL),
            id=jid,
            args=[match.match_id, match.match_slug],
            replace_existing=True,
            misfire_grace_time=config.SCORECARD_POLL_INTERVAL,
        )
        _registry.register(match.match_id, "scorecard")
        log.info("[{}] Scorecard polling started (every {}s)", match.match_id, config.SCORECARD_POLL_INTERVAL)


def _handle_match_end(match_id: str, slug: str) -> None:
    _cancel_job(match_id, "live")
    _cancel_job(match_id, "scorecard")
    _registry.mark_completed(match_id)

    # One final authoritative scorecard
    jid = _job_id(match_id, "final")
    _scheduler.add_job(
        _job_final_scorecard,
        trigger=DateTrigger(run_date=utc_now()),
        id=jid,
        args=[match_id, slug],
        replace_existing=True,
        misfire_grace_time=120,
    )
    log.info("[{}] Match complete — final scorecard scheduled", match_id)


# ── Main poller ───────────────────────────────────────────────────────────────

async def _poll_match_list() -> None:
    """Core polling loop — runs every SCHEDULE_POLL_INTERVAL seconds."""
    log.info("=== Match list poll starting ===")
    try:
        matches = await fetch_match_list()
    except Exception as exc:
        log.error("Failed to fetch match list: {}", exc)
        return

    await save_schedule([m.model_dump(mode="json") for m in matches])

    now = utc_now()

    for match in matches:
        mid = match.match_id

        if not _registry.is_known(mid):
            _registry.mark_known(mid)
            log.info("[{}] New match discovered: {} vs {} ({})", mid, match.team_a, match.team_b, match.status)

        # Always try to scrape static data for new matches
        if not _registry.is_static_done(mid) and match.status != "live":
            _schedule_static(match)

        if match.status == "live":
            # Also make sure static data is fetched
            if not _registry.is_static_done(mid):
                _schedule_static(match)
            _schedule_live_polling(match)

        elif match.status == "upcoming" and match.start_time:
            seconds_to_start = (match.start_time - now).total_seconds()
            if 0 < seconds_to_start <= config.PRE_WARM_SECONDS:
                log.info(
                    "[{}] Match starts in {:.0f}s — pre-warming live polling",
                    mid, seconds_to_start,
                )
                _schedule_live_polling(match)

        elif match.status == "completed" and not _registry.is_completed(mid):
            _handle_match_end(mid, match.match_slug)

    log.info("=== Match list poll complete — {} matches tracked ===", len(matches))


# ── Public API ────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Add the recurring match-list poller and start APScheduler."""
    _scheduler.add_job(
        _poll_match_list,
        trigger=IntervalTrigger(seconds=config.SCHEDULE_POLL_INTERVAL),
        id="match_list_poller",
        next_run_time=utc_now(),           # run immediately on startup
        misfire_grace_time=60,
    )
    _scheduler.start()
    log.info(
        "Scheduler started — polling match list every {}s",
        config.SCHEDULE_POLL_INTERVAL,
    )


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
