"""
scraper/interceptor.py
----------------------
Network-request interception layer for Playwright pages.

How it works
------------
When CREX's Angular app boots, it fires XHR/fetch requests to its backend
API to populate each tab.  We attach a `response` handler to the Playwright
page that captures any JSON response whose URL matches a known pattern.
This gives us the raw, structured API payload — far cleaner and faster
than parsing rendered HTML.

Fallback
--------
If no API response is captured within the timeout window, callers fall back
to DOM parsing (handled inside match_list.py / match_detail.py).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from playwright.async_api import Page, Response

from utils.logger import log


# Known CREX API URL fragments — we capture any response whose URL contains
# one of these substrings.
_API_PATTERNS: list[str] = [
    "/api/",
    "crex.live",
    "goscorer.com",   # primary backend powering CREX (api.goscorer.com)
    "api.crex",       # alternative CREX API subdomain
    "cricwick",       # CDN/backend alias seen on some CREX deployments
    "matchDetail",
    "getSV3",
    "getSC4",
    "getBallFeeds",
    "scorecard",
    "schedules",
    "fixtures",
    "squad",
    "commentary",
]


class APIInterceptor:
    """
    Attach to a Playwright Page before navigation.  Accumulates all JSON
    API responses in `self.captured`.

    Usage::

        interceptor = APIInterceptor(page)
        await interceptor.attach()
        await page.goto(url, wait_until="networkidle")
        responses = interceptor.captured
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self.captured: list[dict[str, Any]] = []

    async def attach(self) -> None:
        self._page.on("response", self._handle_response)

    async def detach(self) -> None:
        self._page.remove_listener("response", self._handle_response)

    async def _handle_response(self, response: Response) -> None:
        url = response.url
        status = response.status

        if status < 200 or status >= 300:
            return

        # Only intercept URLs that look like API endpoints
        if not any(pat in url for pat in _API_PATTERNS):
            return

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return

        try:
            body = await response.json()
            self.captured.append({"url": url, "data": body})
            log.debug("Intercepted API response from: {}", _short_url(url))
        except Exception as exc:
            log.debug("Could not parse intercepted response from {}: {}", url, exc)

    def find(self, keyword: str) -> Optional[Any]:
        """
        Return the first captured response whose URL contains *keyword*.
        Returns the raw parsed JSON body (may be a dict OR a list —
        getSC4 returns a top-level list of innings).
        """
        for item in self.captured:
            if keyword.lower() in item["url"].lower():
                return item["data"]
        return None

    def all_data(self) -> list[dict]:
        return [item["data"] for item in self.captured]


def _short_url(url: str, max_len: int = 80) -> str:
    parsed = urlparse(url)
    short = f"{parsed.netloc}{parsed.path}"
    return short[:max_len] + ("…" if len(short) > max_len else "")


async def wait_for_api_response(
    page: Page,
    url_fragment: str,
    timeout_ms: int = 10_000,
) -> Optional[Any]:
    """
    Wait for a specific API response to arrive after navigation.
    Returns the parsed JSON body or None on timeout.

    Useful for waiting for a particular endpoint rather than scanning
    all intercepted responses.
    """
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()

    async def _handler(response: Response) -> None:
        if url_fragment in response.url and not future.done():
            try:
                data = await response.json()
                future.set_result(data)
            except Exception:
                pass

    page.on("response", _handler)
    try:
        return await asyncio.wait_for(future, timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        log.debug("Timeout waiting for API response containing '{}'", url_fragment)
        return None
    finally:
        page.remove_listener("response", _handler)
