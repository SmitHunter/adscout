import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from adscout.models import Ad, ScrapeProgress, SearchQuery

ProgressCallback = Callable[[ScrapeProgress], Awaitable[None] | None]


async def emit_progress(
    progress: ProgressCallback | None,
    ads: list[Ad],
    phase: Literal["opening", "scrolling", "parsing", "done"],
    message: str,
) -> None:
    """Call a sync or async progress callback with counts derived from ``ads``."""
    if progress is None:
        return
    result = progress(
        ScrapeProgress(
            ads_found=len(ads),
            advertisers=len({k for a in ads if (k := a.advertiser_key)}),
            phase=phase,
            message=message,
        )
    )
    if inspect.isawaitable(result):
        await result


class CancelToken:
    """A cancel flag plus an ``asyncio.Event`` so waits wake up as soon as it is set.

    ``cancel()`` may be called from the loop, from a signal handler or from another
    thread. The event is created lazily in the loop that first waits on the token.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def cancel(self) -> None:
        self._cancelled = True
        event, loop = self._event, self._loop
        if event is None or loop is None:
            return
        # Always schedule, even on the loop's own thread: a signal handler may run
        # this mid-callback, where touching the event's waiters inline is unsafe.
        with contextlib.suppress(RuntimeError):  # the loop closed meanwhile
            loop.call_soon_threadsafe(event.set)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _get_event(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        if self._event is None or self._loop is not loop:
            self._event = asyncio.Event()
            self._loop = loop
        # Re-check after publishing the event: a cancel() racing in from another
        # thread either saw the event or set the flag before this line.
        if self._cancelled:
            self._event.set()
        return self._event

    async def wait(self) -> None:
        """Return once the token is cancelled."""
        await self._get_event().wait()

    async def sleep(self, delay: float) -> bool:
        """Sleep ``delay`` seconds or until cancelled; return True if cancelled."""
        if self._cancelled:
            return True
        try:
            await asyncio.wait_for(self._get_event().wait(), delay)
        except TimeoutError:
            return self._cancelled
        return True


class SourceBlocked(Exception):
    def __init__(self, reason: str, how_to_fix: str) -> None:
        super().__init__(f"{reason} {how_to_fix}")
        self.reason = reason
        self.how_to_fix = how_to_fix


class SourceError(Exception):
    pass


class SearchResult(BaseModel):
    ads: list[Ad] = Field(default_factory=list)
    status: Literal["ok", "blocked", "cancelled", "capped"]
    message: str | None = None


class Source(Protocol):
    name: str

    def supports(self, q: SearchQuery) -> bool: ...

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult: ...
