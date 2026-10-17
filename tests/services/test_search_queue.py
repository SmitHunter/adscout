"""Scheduled searches queue FIFO behind a running one; manual ones still get SearchBusy."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import make_engine, sweep_running
from adscout.db.tables import Snapshot
from adscout.models import Ad, SearchQuery
from adscout.services.searches import SearchBusy, SearchEvent, SearchManager
from adscout.sources import CancelToken, ProgressCallback, SearchResult


def _ad(ad_id: str) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id="p1",
        page_name="Shop",
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        is_active=True,
    )


class Gated:
    """One search per instance; waits for ``gate`` (or a cancel)."""

    name = "fake"

    def __init__(self, keyword: str, order: list[str]) -> None:
        self.keyword = keyword
        self.order = order
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    def supports(self, q: SearchQuery) -> bool:
        return True

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        self.order.append(self.keyword)
        self.started.set()
        assert cancel is not None
        gate = asyncio.ensure_future(self.gate.wait())
        stop = asyncio.ensure_future(cancel.wait())
        await asyncio.wait({gate, stop}, return_when=asyncio.FIRST_COMPLETED)
        gate.cancel()
        stop.cancel()
        if cancel.cancelled:
            return SearchResult(ads=[], status="cancelled", message="Search cancelled.")
        return SearchResult(ads=[_ad("1")], status="ok")


class Factory:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.sources: dict[str, Gated] = {}

    def __call__(self, q: SearchQuery) -> Gated:
        src = Gated(q.keyword, self.order)
        self.sources[q.keyword] = src
        return src


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _status(engine: Engine, sid: int) -> str:
    with Session(engine) as s:
        snap = s.get(Snapshot, sid)
        assert snap is not None
        return snap.status


async def _until(cond: object, tries: int = 300) -> None:
    for _ in range(tries):
        if callable(cond) and cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def test_scheduled_enqueue_waits_for_running_search(engine: Engine) -> None:
    factory = Factory()
    manager = SearchManager(engine, factory, None)
    first = await manager.start(SearchQuery(keyword="alpha"))
    second = await manager.enqueue(SearchQuery(keyword="beta"), "scheduled")
    third = await manager.enqueue(SearchQuery(keyword="gamma"), "scheduled", watch_item_id=7)
    await factory.sources["alpha"].started.wait()

    assert manager.queue_size() == 2
    assert _status(engine, second) == "queued"
    active = manager.active()
    assert active is not None and active.snapshot_id == first and active.queued == 2
    assert factory.order == ["alpha"]

    factory.sources["alpha"].gate.set()
    await _until(lambda: factory.order == ["alpha", "beta"])
    assert _status(engine, first) == "ok"
    assert _status(engine, second) == "running"
    assert manager.queue_size() == 1

    factory.sources["beta"].gate.set()
    await _until(lambda: factory.order == ["alpha", "beta", "gamma"])
    factory.sources["gamma"].gate.set()
    await _until(lambda: manager.active() is None)
    assert [_status(engine, s) for s in (first, second, third)] == ["ok", "ok", "ok"]
    with Session(engine) as s:
        snap = s.get(Snapshot, third)
        assert snap is not None and snap.watch_item_id == 7


async def test_scheduled_enqueue_when_idle_starts_now(engine: Engine) -> None:
    factory = Factory()
    manager = SearchManager(engine, factory, None)
    sid = await manager.enqueue(SearchQuery(keyword="alpha"), "scheduled")
    await factory.sources["alpha"].started.wait()
    assert _status(engine, sid) == "running"
    assert manager.queue_size() == 0
    factory.sources["alpha"].gate.set()
    await _until(lambda: manager.active() is None)


async def test_manual_start_busy_while_queue_non_empty(engine: Engine) -> None:
    factory = Factory()
    manager = SearchManager(engine, factory, None)
    await manager.start(SearchQuery(keyword="alpha"))
    await manager.enqueue(SearchQuery(keyword="beta"), "scheduled")
    with pytest.raises(SearchBusy):
        await manager.start(SearchQuery(keyword="manual"))
    with pytest.raises(SearchBusy):
        await manager.enqueue(SearchQuery(keyword="manual"), "manual")

    factory.sources["alpha"].gate.set()
    await factory.sources["beta"].started.wait()
    # The queue is empty now, but beta is still running.
    with pytest.raises(SearchBusy):
        await manager.start(SearchQuery(keyword="manual"))
    factory.sources["beta"].gate.set()
    await _until(lambda: manager.active() is None)
    await manager.start(SearchQuery(keyword="manual"))
    factory.sources["manual"].gate.set()
    await _until(lambda: manager.active() is None)


async def test_cancel_queued_search_removes_it(engine: Engine) -> None:
    factory = Factory()
    manager = SearchManager(engine, factory, None)
    await manager.start(SearchQuery(keyword="alpha"))
    queued = await manager.enqueue(SearchQuery(keyword="beta"), "scheduled")
    events = manager.subscribe(queued)
    first = await anext(events)
    assert first.kind == "progress" and first.snapshot_id == queued

    assert manager.cancel(queued) is True
    rest: list[SearchEvent] = [e async for e in events]
    assert rest[-1].kind == "cancelled"
    assert manager.queue_size() == 0
    assert _status(engine, queued) == "cancelled"

    factory.sources["alpha"].gate.set()
    await _until(lambda: manager.active() is None)
    assert factory.order == ["alpha"]


async def test_shutdown_cancels_queued(engine: Engine) -> None:
    factory = Factory()
    manager = SearchManager(engine, factory, None)
    first = await manager.start(SearchQuery(keyword="alpha"))
    queued = await manager.enqueue(SearchQuery(keyword="beta"), "scheduled")
    await factory.sources["alpha"].started.wait()
    await asyncio.wait_for(manager.shutdown(), 2)
    assert _status(engine, first) == "cancelled"
    assert _status(engine, queued) == "cancelled"
    assert factory.order == ["alpha"]


def test_sweep_running_also_closes_queued(engine: Engine) -> None:
    with Session(engine) as s:
        snap = Snapshot(
            query="x",
            country="US",
            active_status="active",
            media_type="all",
            source="fake",
            started_at=datetime.now(UTC),
            status="queued",
        )
        s.add(snap)
        s.commit()
        assert sweep_running(s) == 1
        s.refresh(snap)
        assert snap.status == "error"
