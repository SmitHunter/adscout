"""SearchManager: run one search at a time in the server loop and fan out live events.

Manual searches are refused (``SearchBusy``) while another runs or waits; scheduled ones
queue FIFO behind it and start one after another.
"""

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import fail_snapshot, mark_running
from adscout.db.tables import Snapshot
from adscout.models import ScrapeProgress, SearchQuery
from adscout.services.scrape import run_scrape_into, start_snapshot
from adscout.sources import CancelToken, Source

logger = logging.getLogger(__name__)

SearchOrigin = Literal["manual", "scheduled"]
EventKind = Literal["progress", "media", "done", "blocked", "error", "cancelled"]
TERMINAL_KINDS: frozenset[str] = frozenset({"done", "blocked", "error", "cancelled"})

PHASE_TEXT: dict[str, str] = {
    "queued": "Waiting for the current search to finish",
    "opening": "Opening the Ad Library",
    "scrolling": "Scrolling for more ads",
    "parsing": "Reading the page text",
    "media": "Downloading media",
    "done": "Done",
}

_STATUS_KIND: dict[str, EventKind] = {
    "ok": "done",
    "capped": "done",
    "blocked": "blocked",
    "cancelled": "cancelled",
    "error": "error",
}

NOT_RUNNING_MESSAGE = "This search is no longer running."

MediaProgress = Callable[[int, int], Awaitable[None] | None]


class SearchEvent(BaseModel):
    snapshot_id: int
    kind: EventKind
    ads_found: int
    advertisers: int
    phase: str
    message: str | None = None
    # Scheduled searches waiting behind this one (filled in by ``SearchManager.active``).
    queued: int = 0


class SearchBusy(Exception):
    """Raised by ``SearchManager.start`` while another search is running or queued."""


class MediaStep(Protocol):
    async def download_snapshot(
        self,
        engine: Engine,
        snapshot_id: int,
        progress: MediaProgress | None,
        cancel: CancelToken | None,
    ) -> int: ...


class AnalysisStep(Protocol):
    """Starts the angle analysis after a search (``AnalysisManager``)."""

    async def start_after_search(self, snapshot_id: int) -> None: ...


@dataclass
class _Run:
    snapshot_id: int
    q: SearchQuery
    source: Source
    cancel: CancelToken
    last: SearchEvent
    watch_item_id: int | None = None
    subscribers: set[asyncio.Queue[SearchEvent]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


def terminal_event(snap: Snapshot) -> SearchEvent:
    """The final event for a snapshot that is no longer being searched."""
    assert snap.id is not None
    kind = _STATUS_KIND.get(snap.status, "error")
    message = snap.error_message
    if snap.status not in _STATUS_KIND and message is None:
        message = NOT_RUNNING_MESSAGE
    return SearchEvent(
        snapshot_id=snap.id,
        kind=kind,
        ads_found=snap.ad_count,
        advertisers=snap.advertiser_count,
        phase=PHASE_TEXT["done"],
        message=message,
    )


def _placeholder(snapshot_id: int, phase: str) -> SearchEvent:
    return SearchEvent(
        snapshot_id=snapshot_id,
        kind="progress",
        ads_found=0,
        advertisers=0,
        phase=PHASE_TEXT[phase],
    )


class SearchManager:
    """Owns the running search and the scheduled ones queued behind it: start/enqueue,
    cancel, live events, re-attach."""

    def __init__(
        self,
        engine: Engine,
        source_factory: Callable[[SearchQuery], Source],
        media: MediaStep | None,
        *,
        analysis: AnalysisStep | None = None,
    ) -> None:
        self._engine = engine
        self._source_factory = source_factory
        self._media = media
        self._analysis = analysis
        self._run: _Run | None = None
        self._queue: deque[_Run] = deque()
        self._closing = False

    async def start(self, q: SearchQuery) -> int:
        """Start a manual search now and return its snapshot id (``SearchBusy`` if busy)."""
        return await self.enqueue(q, "manual")

    async def enqueue(
        self, q: SearchQuery, origin: SearchOrigin, watch_item_id: int | None = None
    ) -> int:
        """Create the snapshot, start its search or queue it, and return its id.

        A manual search raises ``SearchBusy`` while anything runs or waits. A scheduled one
        waits (snapshot status "queued") behind the running search and earlier ones.
        """
        # No await until the run is registered, so two calls can't both see "idle".
        busy = self._run is not None or bool(self._queue)
        if self._closing or (busy and origin == "manual"):
            raise SearchBusy("A search is already running.")
        source = self._source_factory(q)
        snapshot_id = start_snapshot(
            self._engine, q, source.name, queued=busy, watch_item_id=watch_item_id
        )
        run = _Run(
            snapshot_id=snapshot_id,
            q=q,
            source=source,
            cancel=CancelToken(),
            last=_placeholder(snapshot_id, "queued" if busy else "opening"),
            watch_item_id=watch_item_id,
        )
        if busy:
            self._queue.append(run)
            logger.info("Queued %s search %s (%d waiting)", origin, snapshot_id, len(self._queue))
        else:
            self._launch(run)
        return snapshot_id

    def live_snapshot_ids(self) -> set[int]:
        """Snapshots this manager is running or has queued (this process's live searches)."""
        runs = [self._run, *self._queue] if self._run is not None else list(self._queue)
        return {r.snapshot_id for r in runs}

    def live_watch_items(self) -> set[int]:
        """Watch items whose search this manager is running or has queued."""
        runs = [self._run, *self._queue] if self._run is not None else list(self._queue)
        return {r.watch_item_id for r in runs if r.watch_item_id is not None}

    def queue_size(self) -> int:
        """Scheduled searches waiting behind the running one."""
        return len(self._queue)

    def cancel(self, snapshot_id: int) -> bool:
        """Stop the running search or drop a queued one; False if it is neither."""
        run = self._run
        if run is not None and run.snapshot_id == snapshot_id:
            run.cancel.cancel()
            return True
        queued = self._find(snapshot_id)
        if queued is None:
            return False
        self._queue.remove(queued)
        self._close_queued(queued, "Search cancelled.", "cancelled")
        return True

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the running search (server shutdown): cancel it, then wait for it to end.

        A search that ignores the cancel token for ``timeout`` seconds is cancelled hard.
        Queued searches never start: they are closed as cancelled.
        """
        self._closing = True
        while self._queue:
            self._close_queued(self._queue.popleft(), "Search interrupted.", "cancelled")
        run = self._run
        if run is None or run.task is None:
            return
        run.cancel.cancel()
        task = run.task
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            if not task.done():
                raise
        except Exception:
            logger.exception("Search %s failed while shutting down", run.snapshot_id)

    def active(self) -> SearchEvent | None:
        """The latest event of the running search (with the queue length), or None when idle."""
        run = self._run
        if run is None:
            return None
        return run.last.model_copy(update={"queued": len(self._queue)})

    async def subscribe(self, snapshot_id: int) -> AsyncGenerator[SearchEvent]:
        """Yield the latest event, then live ones, ending after a terminal event.

        A queued search yields its waiting event, then follows the search once it starts.
        A snapshot that is not running or queued yields one terminal event built from the
        DB. Raises ``LookupError`` for an unknown snapshot.
        """
        run = self._find(snapshot_id)
        if run is None:
            yield self._terminal_from_db(snapshot_id)
            return
        queue: asyncio.Queue[SearchEvent] = asyncio.Queue()
        run.subscribers.add(queue)
        try:
            event = run.last
            while True:
                yield event
                if event.kind in TERMINAL_KINDS:
                    return
                event = await queue.get()
        finally:
            run.subscribers.discard(queue)

    def _find(self, snapshot_id: int) -> _Run | None:
        if self._run is not None and self._run.snapshot_id == snapshot_id:
            return self._run
        return next((r for r in self._queue if r.snapshot_id == snapshot_id), None)

    def _launch(self, run: _Run) -> None:
        self._run = run
        run.task = asyncio.create_task(self._execute(run))

    def _start_next(self) -> None:
        """Start the oldest queued search, if any (called as a search ends)."""
        while self._queue and not self._closing:
            run = self._queue.popleft()
            try:
                with Session(self._engine) as session:
                    mark_running(session, run.snapshot_id)
            except Exception:
                logger.exception("Could not start queued search %s", run.snapshot_id)
                self._close_queued(run, "Could not start this search.", "error")
                continue
            self._publish(run, _placeholder(run.snapshot_id, "opening"))
            self._launch(run)
            return

    def _close_queued(self, run: _Run, message: str, status: Literal["cancelled", "error"]) -> None:
        """End a search that never started: close its snapshot and tell its subscribers."""
        try:
            with Session(self._engine) as session:
                fail_snapshot(session, run.snapshot_id, message, status=status)
        except Exception:
            logger.exception("Could not close queued search %s", run.snapshot_id)
        self._publish(
            run,
            SearchEvent(
                snapshot_id=run.snapshot_id,
                kind=status,
                ads_found=0,
                advertisers=0,
                phase=PHASE_TEXT["done"],
                message=message,
            ),
        )

    def _terminal_from_db(self, snapshot_id: int) -> SearchEvent:
        with Session(self._engine) as session:
            snap = session.get(Snapshot, snapshot_id)
            if snap is None:
                raise LookupError(f"snapshot {snapshot_id} not found")
            return terminal_event(snap)

    def _publish(self, run: _Run, event: SearchEvent) -> None:
        run.last = event
        for queue in run.subscribers:
            queue.put_nowait(event)

    async def _execute(self, run: _Run) -> None:
        sid = run.snapshot_id

        def on_progress(p: ScrapeProgress) -> None:
            phase = PHASE_TEXT.get(p.phase, p.phase)
            # The scrape finishing isn't the search finishing while media may follow,
            # and the scrape may still end blocked or cancelled: keep the phase it was
            # in. _download_media announces the media phase once it really starts.
            if p.phase == "done" and self._media is not None:
                phase = run.last.phase
            self._publish(
                run,
                SearchEvent(
                    snapshot_id=sid,
                    kind="progress",
                    ads_found=p.ads_found,
                    advertisers=p.advertisers,
                    phase=phase,
                    message=p.message,
                ),
            )

        try:
            try:
                snap = await run_scrape_into(
                    self._engine, sid, run.q, run.source, on_progress, run.cancel
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # run_scrape_into has already marked the snapshot "error".
                logger.exception("Search %s failed", sid)
            else:
                if snap.status in ("ok", "capped"):
                    if self._media is not None:
                        await self._download_media(run)
                    await self._start_analysis(run)
            self._publish(run, self._terminal_from_db(sid))
        except asyncio.CancelledError:
            # Task cancelled (server shutdown): run_scrape_into marked it cancelled.
            self._publish(
                run,
                SearchEvent(
                    snapshot_id=sid,
                    kind="cancelled",
                    ads_found=run.last.ads_found,
                    advertisers=run.last.advertisers,
                    phase=PHASE_TEXT["done"],
                    message="Search interrupted.",
                ),
            )
            raise
        except Exception as exc:
            logger.exception("Could not read the result of search %s", sid)
            self._publish(
                run,
                SearchEvent(
                    snapshot_id=sid,
                    kind="error",
                    ads_found=run.last.ads_found,
                    advertisers=run.last.advertisers,
                    phase=PHASE_TEXT["done"],
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
        finally:
            if self._run is run:
                self._run = None
                self._start_next()

    async def _download_media(self, run: _Run) -> None:
        assert self._media is not None
        base = run.last

        def publish_media(message: str | None) -> None:
            self._publish(
                run,
                SearchEvent(
                    snapshot_id=run.snapshot_id,
                    kind="media",
                    ads_found=base.ads_found,
                    advertisers=base.advertisers,
                    phase=PHASE_TEXT["media"],
                    message=message,
                ),
            )

        def on_media(done: int, total: int) -> None:
            publish_media(f"{done} of {total} files")

        # Only a scrape that ended ok/capped gets here, so this is the first and only
        # place the media phase is announced.
        publish_media(None)
        try:
            await self._media.download_snapshot(self._engine, run.snapshot_id, on_media, run.cancel)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Media is best effort: the search itself already succeeded.
            logger.exception("Media download failed for snapshot %s", run.snapshot_id)

    async def _start_analysis(self, run: _Run) -> None:
        """Hand the snapshot to auto-analyze as a detached job (best effort).

        The search ends right away: the analysis never holds up the next search and is
        followed through ``GET /api/snapshots/{id}/analysis``, not this event stream.
        """
        if self._analysis is None or run.cancel.cancelled:
            return
        try:
            await self._analysis.start_after_search(run.snapshot_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auto-analyze failed to start for snapshot %s", run.snapshot_id)
