"""
main.py
-------
Entry point for the Crickey real-time cricket scraping system.

Usage
-----
    python main.py                    # run the full scheduler (default)
    python main.py --once             # single match-list poll then exit
    python main.py --match <slug>     # scrape one specific match and exit
    python main.py --match <slug> --tabs info squads scorecard live

The scheduler runs indefinitely until Ctrl+C / SIGTERM.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

import config
from scraper.browser import pool
from scheduler import start_scheduler, stop_scheduler
from scraper.match_list import fetch_match_list
from scraper import match_detail
from storage import (
    save_schedule, save_match_info,
    save_squads, save_scorecard, save_live,
)
from utils.logger import log, setup_logging


# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown_event = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    log.warning("Received signal {} — initiating graceful shutdown…", sig.name)
    _shutdown_event.set()


# ── CLI modes ─────────────────────────────────────────────────────────────────

async def run_scheduler_mode() -> None:
    """Default mode: run the full scheduler indefinitely."""
    await pool.start()
    start_scheduler()

    log.info("Crickey is running. Press Ctrl+C to stop.")
    try:
        await _shutdown_event.wait()
    finally:
        stop_scheduler()
        await pool.stop()
        log.info("Crickey shut down cleanly.")


async def run_once_mode() -> None:
    """Poll the match list once, save output, and exit."""
    await pool.start()
    try:
        log.info("Single poll mode — fetching match list…")
        matches = await fetch_match_list()
        await save_schedule([m.model_dump(mode="json") for m in matches])
        log.info("Done. {} matches saved to {}", len(matches), config.SCHEDULE_FILE)
    finally:
        await pool.stop()


async def run_single_match_mode(slug: str, tabs: list[str]) -> None:
    """Scrape a specific match by slug for the requested tabs."""
    match_id = slug.split("/")[-1]       # last path segment as ID fallback
    await pool.start()
    try:
        if "info" in tabs:
            info = await match_detail.scrape_match_info(match_id, slug)
            await save_match_info(match_id, info.model_dump(mode="json"))

        if "squads" in tabs:
            squads = await match_detail.scrape_squads(match_id, slug)
            await save_squads(match_id, squads.model_dump(mode="json"))

        if "scorecard" in tabs:
            sc = await match_detail.scrape_scorecard(match_id, slug)
            await save_scorecard(match_id, sc.model_dump(mode="json"))

        if "live" in tabs:
            live = await match_detail.scrape_live(match_id, slug)
            await save_live(match_id, live.model_dump(mode="json"))

        log.info("Single-match scrape complete → output/{}/", match_id)
    finally:
        await pool.stop()


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging()

    # Ensure output directory exists
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(
        prog="crickey",
        description="Real-time Cricket Data Scraping System",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch match list once and exit",
    )
    parser.add_argument(
        "--match",
        metavar="SLUG",
        help="Scrape a single match by its URL slug",
    )
    parser.add_argument(
        "--tabs",
        nargs="+",
        choices=["info", "squads", "live", "scorecard"],
        default=["info", "squads", "live", "scorecard"],
        help="Which tabs to scrape (only used with --match)",
    )
    args = parser.parse_args()

    # Register OS signals for graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    try:
        if args.match:
            loop.run_until_complete(run_single_match_mode(args.match, args.tabs))
        elif args.once:
            loop.run_until_complete(run_once_mode())
        else:
            loop.run_until_complete(run_scheduler_mode())
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
