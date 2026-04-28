"""
storage/json_store.py
---------------------
Atomic JSON persistence layer.

Strategy
--------
Every write goes to a temp file first, then os.replace() swaps it in.
This guarantees we never produce a half-written / corrupted JSON file
even if the process is killed mid-write.

Directory layout
----------------
output/
  schedule.json                  ← full fixture list snapshot
  {match_id}/
    match_info.json
    squads.json
    live/
      {ISO-timestamp}.json       ← one file per live poll
    scorecard/
      {ISO-timestamp}.json       ← one file per scorecard poll
    scorecard_latest.json        ← symlink-like: always the freshest scorecard
    live_latest.json             ← always the freshest live snapshot
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from utils.logger import log
import config


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _match_dir(match_id: str) -> Path:
    p = Path(config.OUTPUT_DIR) / match_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _atomic_write(path: Path, data: Any) -> None:
    """Write JSON atomically using a sibling temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def _async_atomic_write(path: Path, data: Any) -> None:
    """Async variant — writes to temp then renames (sync rename is fine)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    async with aiofiles.open(tmp_path, "w", encoding="utf-8") as fh:
        await fh.write(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    os.replace(tmp_path, path)


# ── Public API ────────────────────────────────────────────────────────────────

async def save_schedule(matches: list[dict]) -> None:
    path = Path(config.OUTPUT_DIR) / "schedule.json"
    await _async_atomic_write(path, matches)
    log.debug("Schedule saved -> {}", path)


async def save_match_info(match_id: str, data: dict) -> None:
    path = _match_dir(match_id) / "match_info.json"
    await _async_atomic_write(path, data)
    log.debug("[{}] match_info saved", match_id)


async def save_squads(match_id: str, data: dict) -> None:
    path = _match_dir(match_id) / "squads.json"
    await _async_atomic_write(path, data)
    log.debug("[{}] squads saved", match_id)


async def save_live(match_id: str, data: dict) -> None:
    base = _match_dir(match_id) / "live"
    base.mkdir(exist_ok=True)
    ts = _ts()
    await _async_atomic_write(base / f"{ts}.json", data)
    # keep a "latest" pointer for easy access
    await _async_atomic_write(_match_dir(match_id) / "live_latest.json", data)
    log.debug("[{}] live snapshot saved @ {}", match_id, ts)


async def save_scorecard(match_id: str, data: dict) -> None:
    base = _match_dir(match_id) / "scorecard"
    base.mkdir(exist_ok=True)
    ts = _ts()
    await _async_atomic_write(base / f"{ts}.json", data)
    await _async_atomic_write(_match_dir(match_id) / "scorecard_latest.json", data)
    log.debug("[{}] scorecard snapshot saved @ {}", match_id, ts)


def load_json(path: str | Path) -> Any:
    """Synchronous load — used only in tests / CLI inspection."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
