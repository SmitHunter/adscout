import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import adscout.services.scrape as scrape_mod
from adscout.db.repo import list_snapshots, load_ads, make_engine
from adscout.db.tables import Snapshot
from adscout.models import Ad, SearchQuery
from adscout.services.scrape import run_scrape, run_scrape_into, start_snapshot
from adscout.sources import (
    CancelToken,
    ProgressCallback,
    SearchResult,
    SourceBlocked,
    SourceError,
)

Q = SearchQuery(keyword="posture corrector")


def _ad(ad_id: str) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id="p1",
        page_name="Shop",
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        is_active=True,
    )


class FakeSource:
    name = "fake"

    def __init__(self, result: SearchResult | None = None, exc: BaseException | None = None):
        self.result = result or SearchResult(ads=[_ad("1"), _ad("2")], status="ok")
        self.exc = exc
        self.got: tuple[ProgressCallback | None, CancelToken | None] | None = None

    def supports(self, q: SearchQuery) -> bool:
        return True

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        self.got = (progress, cancel)
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _only_snapshot(engine: Engine) -> Snapshot:
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        return snap


async def test_success_finishes_snapshot(engine: Engine) -> None:
    source = FakeSource()
    cancel = CancelToken()
    progress: ProgressCallback = lambda p: None  # noqa: E731

    snap = await run_scrape(Q, source, engine, progress=progress, cancel=cancel)

    assert snap.status == "ok" and snap.ad_count == 2 and snap.source == "fake"
    assert source.got == (progress, cancel)
    with Session(engine) as s:
        assert len(load_ads(s, snap.id or 0)) == 2


async def test_unexpected_exception_fails_snapshot(engine: Engine) -> None:
    with pytest.raises(SourceError, match="boom") as info:
        await run_scrape(Q, FakeSource(exc=RuntimeError("boom")), engine)
    assert isinstance(info.value.__cause__, RuntimeError)
    snap = _only_snapshot(engine)
    assert snap.status == "error" and "boom" in (snap.error_message or "")


async def test_source_error_fails_snapshot(engine: Engine) -> None:
    with pytest.raises(SourceError, match="crashed"):
        await run_scrape(Q, FakeSource(exc=SourceError("crashed")), engine)
    snap = _only_snapshot(engine)
    assert snap.status == "error" and snap.error_message == "crashed"


async def test_source_blocked_exception_saved_as_blocked(engine: Engine) -> None:
    snap = await run_scrape(Q, FakeSource(exc=SourceBlocked("Login required.", "Sign in.")), engine)
    assert snap.status == "blocked"
    assert snap.error_message == "Login required. Sign in."


async def test_finish_failure_fails_snapshot(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> Snapshot:
        raise ValueError("disk full")

    monkeypatch.setattr(scrape_mod, "finish_snapshot", broken)
    with pytest.raises(SourceError, match="disk full"):
        await run_scrape(Q, FakeSource(), engine)
    snap = _only_snapshot(engine)
    assert snap.status == "error"


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), asyncio.CancelledError()])
async def test_interrupt_marks_cancelled(engine: Engine, exc: BaseException) -> None:
    with pytest.raises(type(exc)):
        await run_scrape(Q, FakeSource(exc=exc), engine)
    snap = _only_snapshot(engine)
    assert snap.status == "cancelled"
    assert snap.finished_at is not None


async def test_run_scrape_wrapper_unchanged(engine: Engine) -> None:
    snap = await run_scrape(Q, FakeSource(), engine)
    assert snap.status == "ok" and snap.ad_count == 2 and snap.query == Q.keyword
    assert _only_snapshot(engine).id == snap.id


async def test_start_snapshot_then_run_scrape_into(engine: Engine) -> None:
    snapshot_id = start_snapshot(engine, Q, "fake")
    running = _only_snapshot(engine)
    assert running.id == snapshot_id and running.status == "running"

    source = FakeSource()
    cancel = CancelToken()
    snap = await run_scrape_into(engine, snapshot_id, Q, source, None, cancel)
    assert snap.id == snapshot_id and snap.status == "ok" and snap.ad_count == 2
    assert source.got == (None, cancel)


async def test_run_scrape_into_marks_error(engine: Engine) -> None:
    snapshot_id = start_snapshot(engine, Q, "fake")
    with pytest.raises(SourceError, match="boom"):
        await run_scrape_into(engine, snapshot_id, Q, FakeSource(exc=RuntimeError("boom")))
    assert _only_snapshot(engine).status == "error"
