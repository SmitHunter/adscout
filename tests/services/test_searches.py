import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import create_snapshot, list_snapshots, make_engine, sweep_running
from adscout.db.tables import Snapshot
from adscout.models import Ad, SearchQuery
from adscout.services.searches import SearchBusy, SearchEvent, SearchManager
from adscout.sources import CancelToken, ProgressCallback, SearchResult, Source
from adscout.sources.base import emit_progress

Q = SearchQuery(keyword="posture corrector")


def _ad(ad_id: str, page: str = "p1") -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=page,
        page_name="Shop",
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        is_active=True,
    )


class GatedSource:
    """Emits progress, then waits for ``gate`` (or a cancel) before returning."""

    name = "fake"

    def __init__(self, status: str = "ok", exc: BaseException | None = None) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.status = status
        self.exc = exc

    def supports(self, q: SearchQuery) -> bool:
        return True

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        ads = [_ad("1"), _ad("2", "p2")]
        await emit_progress(progress, [], "opening", "Opening")
        await emit_progress(progress, ads, "scrolling", "2 ads so far")
        self.started.set()
        assert cancel is not None
        gate = asyncio.ensure_future(self.gate.wait())
        stop = asyncio.ensure_future(cancel.wait())
        await asyncio.wait({gate, stop}, return_when=asyncio.FIRST_COMPLETED)
        gate.cancel()
        stop.cancel()
        if self.exc is not None:
            raise self.exc
        if cancel.cancelled:
            return SearchResult(ads=ads, status="cancelled", message="Search cancelled.")
        await emit_progress(progress, ads, "done", "Found 2 ads")
        return SearchResult(ads=ads, status=self.status)


class SleepySource:
    """Waits in the cancel-aware sleep like MetaWebSource does between scrolls."""

    name = "fake"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    def supports(self, q: SearchQuery) -> bool:
        return True

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        assert cancel is not None
        self.started.set()
        await cancel.sleep(30)
        status = "cancelled" if cancel.cancelled else "ok"
        return SearchResult(ads=[_ad("1")], status=status)


class FakeMedia:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[int] = []

    async def download_snapshot(
        self,
        engine: Engine,
        snapshot_id: int,
        progress: Callable[[int, int], Awaitable[None] | None] | None,
        cancel: CancelToken | None,
    ) -> int:
        self.calls.append(snapshot_id)
        assert progress is not None
        for done in range(1, 3):
            result = progress(done, 2)
            if result is not None:
                await result
        if self.fail:
            raise RuntimeError("media boom")
        return 2


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _manager(engine: Engine, source: Source, media: FakeMedia | None = None) -> SearchManager:
    return SearchManager(engine, lambda q: source, media)


async def _collect(events: AsyncIterator[SearchEvent]) -> list[SearchEvent]:
    return [e async for e in events]


async def _wait_until_idle(manager: SearchManager) -> None:
    for _ in range(200):
        if manager.active() is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("search did not finish")


async def test_start_creates_running_snapshot_and_active_event(engine: Engine) -> None:
    source = GatedSource()
    manager = _manager(engine, source)
    sid = await manager.start(Q)
    with Session(engine) as s:
        snap = s.get(Snapshot, sid)
        assert snap is not None and snap.status == "running"
    active = manager.active()
    assert active is not None and active.snapshot_id == sid and active.kind == "progress"
    source.gate.set()
    await _wait_until_idle(manager)


async def test_second_start_raises_busy(engine: Engine) -> None:
    source = GatedSource()
    manager = _manager(engine, source)
    await manager.start(Q)
    with pytest.raises(SearchBusy):
        await manager.start(Q)
    source.gate.set()
    await _wait_until_idle(manager)
    # Free again once the first search finished.
    second = await manager.start(Q)
    source.gate.set()
    await _wait_until_idle(manager)
    assert second > 0


async def test_subscribe_replays_latest_then_live(engine: Engine) -> None:
    source = GatedSource()
    manager = _manager(engine, source)
    sid = await manager.start(Q)
    await source.started.wait()

    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await asyncio.sleep(0.01)
    source.gate.set()
    events = await asyncio.wait_for(task, 2)

    first = events[0]
    assert first.kind == "progress"
    assert first.phase == "Scrolling for more ads"
    assert first.ads_found == 2 and first.advertisers == 2
    assert first.message == "2 ads so far"
    assert [e.phase for e in events[1:-1]] == ["Done"]
    last = events[-1]
    assert last.kind == "done" and last.snapshot_id == sid
    assert last.ads_found == 2 and last.advertisers == 2
    assert manager.active() is None


async def test_phase_text_mapping(engine: Engine) -> None:
    source = GatedSource()
    manager = _manager(engine, source)
    sid = await manager.start(Q)
    # Subscribe before the source runs: the first event is the start placeholder.
    stream = manager.subscribe(sid)
    first = await anext(stream)
    task = asyncio.ensure_future(_collect(stream))
    await source.started.wait()
    source.gate.set()
    events = [first, *await asyncio.wait_for(task, 2)]
    assert [e.phase for e in events] == [
        "Opening the Ad Library",
        "Opening the Ad Library",
        "Scrolling for more ads",
        "Done",
        "Done",
    ]


async def test_subscribe_after_finish_yields_terminal_from_db(engine: Engine) -> None:
    source = GatedSource(status="capped")
    manager = _manager(engine, source)
    sid = await manager.start(Q)
    source.gate.set()
    await _wait_until_idle(manager)

    events = await _collect(manager.subscribe(sid))
    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].ads_found == 2 and events[0].advertisers == 2
    assert events[0].phase == "Done"


@pytest.mark.parametrize(
    ("status", "kind"),
    [("ok", "done"), ("capped", "done"), ("blocked", "blocked"), ("cancelled", "cancelled")],
)
async def test_terminal_kind_from_db_status(engine: Engine, status: str, kind: str) -> None:
    from adscout.db.repo import finish_snapshot

    with Session(engine) as s:
        snap = create_snapshot(s, Q, "fake")
        assert snap.id is not None
        finish_snapshot(
            s,
            snap.id,
            SearchResult(ads=[_ad("1")], status=status, message="msg"),
        )
    manager = _manager(engine, GatedSource())
    [event] = await _collect(manager.subscribe(snap.id))
    assert event.kind == kind and event.message == "msg" and event.ads_found == 1


async def test_error_search_ends_with_error_event(engine: Engine) -> None:
    source = GatedSource(exc=RuntimeError("boom"))
    media = FakeMedia()
    manager = _manager(engine, source, media)
    sid = await manager.start(Q)
    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await source.started.wait()
    source.gate.set()
    events = await asyncio.wait_for(task, 2)
    assert events[-1].kind == "error"
    assert "boom" in (events[-1].message or "")
    assert media.calls == []
    assert manager.active() is None


async def test_cancel_within_one_second(engine: Engine) -> None:
    source = SleepySource()
    media = FakeMedia()
    manager = _manager(engine, source, media)
    sid = await manager.start(Q)
    await source.started.wait()
    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await asyncio.sleep(0.01)

    assert not manager.cancel(sid + 1)
    started = time.monotonic()
    assert manager.cancel(sid)
    events = await asyncio.wait_for(task, 1)
    assert time.monotonic() - started < 1
    assert events[-1].kind == "cancelled"
    assert media.calls == []
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert snap.status == "cancelled" and snap.ad_count == 1


async def test_events_include_media_phase_when_downloader_present(engine: Engine) -> None:
    source = GatedSource()
    media = FakeMedia()
    manager = _manager(engine, source, media)
    sid = await manager.start(Q)
    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await source.started.wait()
    source.gate.set()
    events = await asyncio.wait_for(task, 2)

    media_events = [e for e in events if e.kind == "media"]
    # One event as the media phase starts, then one per file.
    assert len(media_events) == 3
    assert all(e.phase == "Downloading media" for e in media_events)
    assert media_events[0].ads_found == 2 and media_events[0].advertisers == 2
    assert media_events[0].message is None
    assert media_events[-1].message == "2 of 2 files"
    assert events[-1].kind == "done"
    assert media.calls == [sid]


async def test_scrape_end_reads_as_media_phase_while_media_follows(engine: Engine) -> None:
    # "Done" only once the whole search is done: with media still to fetch, the
    # scraper's own "done" step already reads as the media phase.
    source = GatedSource()
    manager = _manager(engine, source, FakeMedia())
    sid = await manager.start(Q)
    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await source.started.wait()
    source.gate.set()
    events = await asyncio.wait_for(task, 2)

    running = [e for e in events if e.kind in ("progress", "media")]
    assert "Done" not in [e.phase for e in running]
    scrape_end = next(e for e in running if e.message == "Found 2 ads")
    # The scrape's own "done" keeps the phase it was in; the media phase is only
    # announced once the download actually starts.
    assert scrape_end.kind == "progress" and scrape_end.phase == "Scrolling for more ads"
    following = running[running.index(scrape_end) + 1]
    assert following.kind == "media" and following.phase == "Downloading media"
    assert events[-1].phase == "Done"


class BlockedAfterDoneSource(GatedSource):
    """Reports its "done" step, then turns out blocked (like a login wall at the end)."""

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        ads = [_ad("1")]
        await emit_progress(progress, ads, "scrolling", "1 ad so far")
        await emit_progress(progress, ads, "done", "Found 1 ad")
        return SearchResult(ads=ads, status="blocked", message="Login required.")


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_blocked_or_cancelled_scrape_never_shows_media_phase(
    engine: Engine, cancel_first: bool
) -> None:
    source: Source = SleepySource() if cancel_first else BlockedAfterDoneSource()
    manager = _manager(engine, source, FakeMedia())
    sid = await manager.start(Q)
    # Subscribe before the source runs so every live event is seen.
    stream = manager.subscribe(sid)
    first = await anext(stream)
    task = asyncio.ensure_future(_collect(stream))
    if isinstance(source, SleepySource):
        await source.started.wait()
        manager.cancel(sid)
    events = [first, *await asyncio.wait_for(task, 2)]
    assert len(events) > 2 or cancel_first
    assert events[-1].kind == ("cancelled" if cancel_first else "blocked")
    assert all(e.phase != "Downloading media" for e in events)


async def test_media_failure_does_not_fail_search(engine: Engine) -> None:
    source = GatedSource()
    media = FakeMedia(fail=True)
    manager = _manager(engine, source, media)
    sid = await manager.start(Q)
    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await source.started.wait()
    source.gate.set()
    events = await asyncio.wait_for(task, 2)
    assert events[-1].kind == "done"
    with Session(engine) as s:
        snap = s.get(Snapshot, sid)
        assert snap is not None and snap.status == "ok"


async def test_blocked_search_skips_media(engine: Engine) -> None:
    source = GatedSource(status="blocked")
    media = FakeMedia()
    manager = _manager(engine, source, media)
    sid = await manager.start(Q)
    task = asyncio.ensure_future(_collect(manager.subscribe(sid)))
    await source.started.wait()
    source.gate.set()
    events = await asyncio.wait_for(task, 2)
    assert events[-1].kind == "blocked"
    assert media.calls == []


async def test_subscribe_unknown_snapshot_raises(engine: Engine) -> None:
    manager = _manager(engine, GatedSource())
    with pytest.raises(LookupError):
        await _collect(manager.subscribe(999))


def test_sweep_running_marks_error(engine: Engine) -> None:
    with Session(engine) as s:
        running = create_snapshot(s, Q, "fake")
        other = create_snapshot(s, Q, "fake")
        assert other.id is not None
        from adscout.db.repo import fail_snapshot

        fail_snapshot(s, other.id, "x", status="cancelled")
        assert sweep_running(s) == 1
        assert sweep_running(s) == 0
        s.refresh(running)
        assert running.status == "error"
        assert running.error_message == "Interrupted: AdScout was closed during this search."
        assert running.finished_at is not None
        s.refresh(other)
        assert other.status == "cancelled"


async def test_shutdown_cancels_and_awaits_running_search(engine: Engine) -> None:
    source = SleepySource()
    manager = _manager(engine, source)
    sid = await manager.start(Q)
    await source.started.wait()
    await asyncio.wait_for(manager.shutdown(), 2)
    assert manager.active() is None
    with Session(engine) as s:
        snap = s.get(Snapshot, sid)
        assert snap is not None and snap.status == "cancelled"


async def test_shutdown_hard_cancels_stuck_search(engine: Engine) -> None:
    class StuckSource(SleepySource):
        async def search(
            self,
            q: SearchQuery,
            progress: ProgressCallback | None = None,
            cancel: CancelToken | None = None,
        ) -> SearchResult:
            self.started.set()
            await asyncio.sleep(30)  # ignores the cancel token
            raise AssertionError("unreachable")

    source = StuckSource()
    manager = _manager(engine, source)
    sid = await manager.start(Q)
    await source.started.wait()
    await asyncio.wait_for(manager.shutdown(timeout=0.1), 2)
    assert manager.active() is None
    with Session(engine) as s:
        snap = s.get(Snapshot, sid)
        assert snap is not None and snap.status != "running"


async def test_shutdown_when_idle_is_noop(engine: Engine) -> None:
    manager = _manager(engine, GatedSource())
    await manager.shutdown()
    assert manager.active() is None
