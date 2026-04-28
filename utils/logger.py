"""
utils/logger.py
---------------
Configures loguru for structured, coloured console output + rotating file log.
Import `log` anywhere in the project instead of using the stdlib logging module.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

import config


def setup_logging() -> None:
    """Call once from main.py to initialise all log sinks."""
    logger.remove()  # drop default stderr sink

    # ── Console (human-readable, coloured) ───────────────────────────────────
    logger.add(
        sys.stdout,
        level=config.LOG_LEVEL,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
    )

    # ── Rotating file (machine-readable JSON-ish) ─────────────────────────────
    log_path = Path(config.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path),
        level="DEBUG",
        rotation=config.LOG_ROTATION,
        retention="7 days",
        compression="zip",
        enqueue=True,   # thread-safe async writes
        format="{time:YYYY-MM-DDTHH:mm:ss.SSS}Z | {level} | {name}:{function}:{line} | {message}",
    )


# Convenience re-export so callers just do: from utils.logger import log
log = logger
