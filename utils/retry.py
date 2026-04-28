"""
utils/retry.py
--------------
Async exponential back-off decorator.

Usage:
    @retry(max_attempts=3, base_delay=2.0)
    async def flaky_fetch():
        ...
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Type

from utils.logger import log


def retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    """
    Decorator factory for async functions.

    Args:
        max_attempts: Total number of tries (including the first).
        base_delay:   Seconds to wait before retry 1; doubles each attempt.
        exceptions:   Exception types to catch and retry on.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        log.error(
                            "All {} attempts failed for '{}': {}",
                            max_attempts, func.__qualname__, exc,
                        )
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "Attempt {}/{} failed for '{}' — retrying in {:.1f}s. Error: {}",
                        attempt, max_attempts, func.__qualname__, delay, exc,
                    )
                    await asyncio.sleep(delay)
            raise RuntimeError("Unreachable") from last_exc  # pragma: no cover
        return wrapper
    return decorator
