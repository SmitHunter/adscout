"""Browser driver abstraction for the Meta Ad Library web source."""

import asyncio
import logging
from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol

from filelock import BaseFileLock
from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    Response,
    ViewportSize,
    async_playwright,
)

from adscout.locks import acquire_profile_async, profile_lock, profile_lock_dir
from adscout.sources.base import SourceError

logger = logging.getLogger(__name__)

_GRAPHQL_PATH = "/api/graphql"
_AD_MARKER = "ad_archive_id"
_LOAD_TIMEOUT_MS = 45_000
_POST_SCROLL_WAIT_MS = 1_500
_VIEWPORT: ViewportSize = {"width": 1280, "height": 900}
CHROMIUM_MISSING = "Chromium isn't installed. Run: uv run playwright install chromium"


async def lock_profile(profile_dir: Path) -> BaseFileLock:
    """Take the profile lock for a browser's whole lifetime; raises ``ProfileBusy``."""
    lock = profile_lock(profile_lock_dir(profile_dir))
    await acquire_profile_async(lock)
    return lock


async def launch_context(pw: Playwright, profile_dir: Path, headless: bool) -> BrowserContext:
    """Launch Chromium on the persistent AdScout profile (shared by scraper and login).

    The caller must hold the profile lock (``lock_profile``) until the context is closed.
    Raises ``SourceError`` with an install hint when Chromium isn't installed.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport=_VIEWPORT,
            locale="en-US",
            # Ctrl+C is handled by the CLI (graceful cancel), not by killing the browser.
            handle_sigint=False,
        )
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            raise SourceError(CHROMIUM_MISSING) from exc
        raise


async def open_until_closed(profile_dir: Path, url: str) -> None:  # pragma: no cover
    """Open the profile headful at ``url`` and return once the user closes the window.

    Raises ``ProfileBusy`` when another AdScout process is using the profile.
    """
    lock = await lock_profile(profile_dir)
    try:
        async with async_playwright() as pw:
            context = await launch_context(pw, profile_dir, headless=False)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, timeout=_LOAD_TIMEOUT_MS)
            await context.wait_for_event("close", timeout=0)
    finally:
        lock.release()


class PageDriver(Protocol):
    async def open(self, url: str) -> None: ...

    async def scroll(self) -> None: ...

    async def settle(self) -> None:
        """Wait for in-flight response body reads so ``drain_payloads`` sees them."""
        ...

    def drain_payloads(self) -> list[str]:
        """Captured response bodies + embedded page HTML since the last call."""
        ...

    async def inner_text(self) -> str: ...

    async def current_url(self) -> str: ...

    async def close(self) -> None: ...


_CLOSED_DURING_OPEN = "The browser was closed while it was opening."


async def _quietly(step: Awaitable[object]) -> None:
    """Tear-down that must not raise (the browser may already be gone)."""
    try:
        await step
    except Exception as exc:
        logger.debug("Ignored tear-down error: %s", exc)


class PlaywrightDriver:  # pragma: no cover - needs a real browser (live-verified in Task 9)
    """Thin Playwright wrapper using a persistent (reusable) browser profile."""

    def __init__(self, profile_dir: Path, headless: bool) -> None:
        self._profile_dir = profile_dir
        self._headless = headless
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._pending: list[str] = []
        self._reads: set[asyncio.Task[None]] = set()
        self._lock: BaseFileLock | None = None
        self._closed = False

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("PlaywrightDriver.open() has not been called")
        return self._page

    async def open(self, url: str) -> None:
        """Lock the profile, launch the browser and load ``url``.

        A search cancelled while this runs calls ``close()`` without waiting for it, and a
        browser launch doesn't always stop when cancelled: whatever this starts after
        ``close()`` is torn down here, so no browser outlives the closed driver.
        """
        # Held until close(): no other process may launch the profile meanwhile.
        lock = await lock_profile(self._profile_dir)
        if self._closed:
            lock.release()
            raise RuntimeError(_CLOSED_DURING_OPEN)
        self._lock = lock
        pw = await async_playwright().start()
        if self._closed:
            await _quietly(pw.stop())
            raise RuntimeError(_CLOSED_DURING_OPEN)
        self._pw = pw
        context = await launch_context(pw, self._profile_dir, self._headless)
        if self._closed:
            await _quietly(context.close())
            raise RuntimeError(_CLOSED_DURING_OPEN)
        self._context = context
        self._page = context.pages[0] if context.pages else await context.new_page()
        self._page.on("response", self._on_response)
        await self._page.goto(url, wait_until="load", timeout=_LOAD_TIMEOUT_MS)
        # Page 1 of results is embedded as JSON in the HTML, not fetched by XHR.
        self._pending.append(await self._page.content())

    def _on_response(self, response: Response) -> None:
        if _GRAPHQL_PATH not in response.url:
            return
        task = asyncio.get_running_loop().create_task(self._read_body(response))
        self._reads.add(task)
        task.add_done_callback(self._reads.discard)

    async def _read_body(self, response: Response) -> None:
        try:
            text = await response.text()
        except Exception as exc:
            logger.debug("Could not read %s: %s", response.url, exc)
            return
        if _AD_MARKER in text:
            self._pending.append(text)

    async def scroll(self) -> None:
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give the pagination XHR a moment to fire before the next drain.
        await self.page.wait_for_timeout(_POST_SCROLL_WAIT_MS)

    async def settle(self) -> None:
        if self._reads:
            await asyncio.gather(*list(self._reads), return_exceptions=True)

    def drain_payloads(self) -> list[str]:
        out, self._pending = self._pending, []
        return out

    async def inner_text(self) -> str:
        return await self.page.inner_text("body")

    async def current_url(self) -> str:
        return self.page.url

    async def close(self) -> None:
        self._closed = True
        reads = list(self._reads)
        for task in reads:
            task.cancel()
        if reads:
            await asyncio.gather(*reads, return_exceptions=True)
        self._reads.clear()
        try:
            if self._context is not None:
                await self._context.close()
        finally:
            self._context = None
            self._page = None
            try:
                if self._pw is not None:
                    await self._pw.stop()
                    self._pw = None
            finally:
                if self._lock is not None:
                    self._lock.release()
                    self._lock = None
