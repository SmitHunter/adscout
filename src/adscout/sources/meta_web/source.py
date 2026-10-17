"""MetaWebSource: polite scrolling scrape of the public Meta Ad Library web UI."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Literal

from adscout.config import Settings
from adscout.models import ALL_COUNTRIES, Ad, SearchQuery
from adscout.sources import CancelToken, ProgressCallback, SearchResult, SourceError
from adscout.sources.base import emit_progress
from adscout.sources.meta_web.blocks import detect_block
from adscout.sources.meta_web.driver import PageDriver, PlaywrightDriver
from adscout.sources.meta_web.parse_graphql import parse_payloads
from adscout.sources.meta_web.parse_text import parse_inner_text
from adscout.sources.meta_web.urls import build_library_url

logger = logging.getLogger(__name__)

MAX_STALLS = 3

Phase = Literal["opening", "scrolling", "parsing", "done"]


class MetaWebSource:
    name = "meta_web"

    def __init__(
        self,
        settings: Settings,
        driver_factory: Callable[[], PageDriver] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._driver_factory = driver_factory or self._default_driver
        self._sleep = sleep
        self._rng = rng or random.Random()

    def _default_driver(self) -> PageDriver:  # pragma: no cover - real browser
        return PlaywrightDriver(self._settings.browser_profile_dir, self._settings.headless)

    def supports(self, q: SearchQuery) -> bool:
        # The web UI has no EU option; the official API source covers EU. Advertiser
        # (page-view) searches may also run across all countries.
        if q.kind == "advertiser" and q.country == ALL_COUNTRIES:
            return True
        return len(q.country) == 2 and q.country.upper() != "EU"

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        # One search at a time is enforced by the caller (SearchManager / a CLI process).
        driver = self._driver_factory()
        try:
            return await self._run(driver, q, progress, cancel)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"Meta Ad Library scrape failed: {exc}") from exc
        finally:
            try:
                await driver.close()
            except Exception as exc:
                logger.warning("Failed to close browser driver: %s", exc)

    async def _pause(self, delay: float, cancel: CancelToken | None) -> None:
        """Polite delay between scrolls; without an injected sleep it wakes on cancel."""
        if self._sleep is not None:
            await self._sleep(delay)
        elif cancel is not None:
            await cancel.sleep(delay)
        else:
            await asyncio.sleep(delay)

    async def _run(
        self,
        driver: PageDriver,
        q: SearchQuery,
        progress: ProgressCallback | None,
        cancel: CancelToken | None,
    ) -> SearchResult:
        cap = min(q.limit, self._settings.max_results)
        collected: dict[str, Ad] = {}

        async def report(phase: Phase, message: str) -> None:
            await emit_progress(progress, list(collected.values()), phase, message)

        def done(
            status: Literal["ok", "blocked", "cancelled", "capped"], message: str | None = None
        ) -> SearchResult:
            return SearchResult(ads=list(collected.values())[:cap], status=status, message=message)

        async def merge() -> int:
            await driver.settle()
            added = 0
            for ad in parse_payloads(driver.drain_payloads()):
                if ad.ad_archive_id not in collected:
                    collected[ad.ad_archive_id] = ad
                    added += 1
            return added

        async def blocked() -> SearchResult | None:
            block = detect_block(await driver.current_url(), await driver.inner_text())
            if block is None:
                return None
            # Keep whatever the last page delivered before the wall appeared.
            await merge()
            await report("done", str(block))
            return done("blocked", f"{block.reason} {block.how_to_fix}")

        await report("opening", f"Opening Meta Ad Library for {q.keyword!r} ({q.country})")
        if not await _open_unless_cancelled(driver, build_library_url(q), cancel):
            await report("done", "Cancelled")
            return done("cancelled", "Search cancelled.")
        if (result := await blocked()) is not None:
            return result

        stalls = 0
        scrolled = False
        while True:
            added = await merge()
            await report("scrolling", f"{len(collected)} ads so far")

            if len(collected) >= cap:
                await report("done", f"Reached the limit of {cap} ads")
                return done("capped", f"Stopped at the limit of {cap} ads.")
            if cancel is not None and cancel.cancelled:
                await report("done", "Cancelled")
                return done("cancelled", "Search cancelled.")
            if scrolled:
                stalls = 0 if added else stalls + 1
            if stalls >= MAX_STALLS:
                break

            delay = self._rng.uniform(
                self._settings.scroll_delay_min, self._settings.scroll_delay_max
            )
            await self._pause(delay, cancel)
            if cancel is not None and cancel.cancelled:
                continue  # merge + report, then the check above returns "cancelled"
            await driver.scroll()
            scrolled = True
            if (result := await blocked()) is not None:
                return result

        if not collected:
            await report("parsing", "No structured results; reading page text")
            for ad in parse_inner_text(await driver.inner_text()):
                collected.setdefault(ad.ad_archive_id, ad)
            if len(collected) >= cap:
                await report("done", f"Reached the limit of {cap} ads")
                return done("capped", f"Stopped at the limit of {cap} ads.")

        await report("done", f"Found {len(collected)} ads")
        return done("ok")


async def _open_unless_cancelled(driver: PageDriver, url: str, cancel: CancelToken | None) -> bool:
    """Open ``url``; return False if ``cancel`` fired first (the open is abandoned).

    The caller closes the driver afterwards, which tears down the browser.
    """
    if cancel is None:
        await driver.open(url)
        return True
    opening = asyncio.ensure_future(driver.open(url))
    stop = asyncio.ensure_future(cancel.wait())
    try:
        await asyncio.wait({opening, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.cancel()
        if not opening.done():
            opening.cancel()
    if opening.done() and not opening.cancelled():
        opening.result()  # re-raise open errors
        return True
    # Give the cancelled open a moment to unwind; closing the driver finishes it off.
    await asyncio.wait({opening}, timeout=1.0)
    opening.add_done_callback(_ignore_result)
    return False


def _ignore_result(task: asyncio.Future[None]) -> None:
    if not task.cancelled():
        task.exception()
