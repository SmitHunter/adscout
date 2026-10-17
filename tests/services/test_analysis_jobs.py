import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.analysis.backends import UNAVAILABLE, AnalysisError, Analyzer, BackendName
from adscout.analysis.models import AngleAnalysis
from adscout.db.repo import create_snapshot, finish_snapshot, latest_analysis, make_engine
from adscout.models import Ad, SearchQuery
from adscout.services.analysis import NoRelevantAds
from adscout.services.analysis_jobs import AnalysisJob, AnalysisManager, AnalysisUnavailable
from adscout.services.searches import SearchEvent, SearchManager
from adscout.sources import CancelToken, ProgressCallback, SearchResult

NOW = datetime(2026, 10, 15, tzinfo=UTC)
KEYWORD = "posture corrector"


def _ad(ad_id: str, page: str, month: int, body: str | None = None) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=f"pid-{page.split()[0].lower()}",
        page_name=page,
        start_date=datetime(2026, month, 5, 3, 0, tzinfo=UTC),
        is_active=True,
        body=body if body is not None else f"Best posture corrector, offer {ad_id}",
    )


ADS = [_ad("1", "Alpha Store", 3), _ad("2", "Alpha Store", 5), _ad("3", "Beta Shop", 8)]
OFF_TOPIC = [_ad("9", "Gamma Co", 10, body="Yoga mats on sale")]

RESULT: dict[str, Any] = {
    "angles": [
        {
            "name": "Back pain relief",
            "description": "Relief from desk-job back pain.",
            "emotion": "pain_relief",
            "ad_ids": ["1", "2", "3"],
        }
    ],
    "hooks": [],
    "offers": [],
    "not_product_ad_ids": [],
    "seasonality": "evergreen",
    "seasonality_note": "No seasonal cues.",
    "verdict": "One. Two. Three.",
}


class FakeAnalyzer:
    def __init__(self, fail: str | None = None) -> None:
        self.name: BackendName = "cli"
        self.model = "claude-sonnet-5-5"
        self.calls = 0
        self.gate: asyncio.Event | None = None
        self.fail = fail

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise AnalysisError(self.fail)
        return AngleAnalysis.model_validate(RESULT), None


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _seed(engine: Engine, ads: list[Ad]) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD, country="US"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=ads, status="ok"))
        return snap.id


def _manager(engine: Engine, analyzer: Analyzer | None, auto: bool = False) -> AnalysisManager:
    def factory() -> Analyzer:
        if analyzer is None:
            raise AnalysisUnavailable(UNAVAILABLE)
        return analyzer

    return AnalysisManager(engine, factory, auto_enabled=lambda: auto, now=lambda: NOW)


async def _finish(manager: AnalysisManager, sid: int) -> AnalysisJob:
    for _ in range(300):
        job = manager.status(sid)
        if job is not None and job.state != "running":
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("analysis did not finish")


async def test_start_runs_and_status_reads_done_from_db(engine: Engine) -> None:
    sid = _seed(engine, ADS)
    analyzer = FakeAnalyzer()
    analyzer.gate = asyncio.Event()
    manager = _manager(engine, analyzer)
    job = await manager.start(sid, force=False)
    assert job.state == "running" and job.creatives == 3
    assert job.model == "claude-sonnet-5-5" and job.backend == "cli"
    assert job.started_at == NOW
    analyzer.gate.set()
    done = await _finish(manager, sid)
    assert done.state == "done" and done.creatives == 3

    # A fresh manager (server restart) still reports the stored analysis.
    fresh = _manager(engine, analyzer).status(sid)
    assert fresh is not None and fresh.state == "done" and fresh.model == "claude-sonnet-5-5"
    assert fresh.finished_at is not None


async def test_start_returns_running_job_for_same_snapshot(engine: Engine) -> None:
    sid = _seed(engine, ADS)
    analyzer = FakeAnalyzer()
    analyzer.gate = asyncio.Event()
    manager = _manager(engine, analyzer)
    first = await manager.start(sid, force=False)
    second = await manager.start(sid, force=True)
    assert second is first
    analyzer.gate.set()
    await _finish(manager, sid)
    assert analyzer.calls == 1


async def test_status_none_without_analysis(engine: Engine) -> None:
    sid = _seed(engine, ADS)
    assert _manager(engine, FakeAnalyzer()).status(sid) is None


async def test_errors_raise_before_a_job_starts(engine: Engine) -> None:
    manager = _manager(engine, None)
    with pytest.raises(LookupError):
        await manager.start(999, force=False)
    with pytest.raises(NoRelevantAds):
        await manager.start(_seed(engine, OFF_TOPIC), force=False)
    with pytest.raises(AnalysisUnavailable) as info:
        await manager.start(_seed(engine, ADS), force=False)
    assert info.value.message == UNAVAILABLE


async def test_analysis_error_message_on_job(engine: Engine) -> None:
    sid = _seed(engine, ADS)
    msg = "Claude Code isn't logged in. Run claude once in a terminal to sign in."
    manager = _manager(engine, FakeAnalyzer(fail=msg))
    await manager.start(sid, force=False)
    job = await _finish(manager, sid)
    assert job.state == "error" and job.message == msg


async def test_shutdown_cancels_running_jobs(engine: Engine) -> None:
    sid = _seed(engine, ADS)
    analyzer = FakeAnalyzer()
    analyzer.gate = asyncio.Event()
    manager = _manager(engine, analyzer)
    await manager.start(sid, force=False)
    await manager.shutdown()
    job = manager.status(sid)
    assert job is not None and job.state == "error"


# --- Auto-analyze after a search ------------------------------------------------------------


class QuickSource:
    name = "fake"

    def __init__(self, ads: list[Ad]) -> None:
        self.ads = ads

    def supports(self, q: SearchQuery) -> bool:
        return True

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        return SearchResult(ads=self.ads, status="ok")


async def _search(manager: SearchManager) -> tuple[int, list[SearchEvent]]:
    sid = await manager.start(SearchQuery(keyword=KEYWORD, country="US"))
    events = [e async for e in manager.subscribe(sid)]
    return sid, events


async def test_auto_analyze_runs_after_search_when_enabled(engine: Engine) -> None:
    analyzer = FakeAnalyzer()
    analyzer.gate = asyncio.Event()
    analysis = _manager(engine, analyzer, auto=True)
    searches = SearchManager(engine, lambda q: QuickSource(ADS), None, analysis=analysis)
    # The search ends while Claude is still working: the analysis is a detached job.
    sid, events = await asyncio.wait_for(_search(searches), 2)
    assert events[-1].kind == "done"
    assert all(e.kind in ("progress", "done") for e in events)
    assert searches.active() is None
    job = analysis.status(sid)
    assert job is not None and job.state == "running"

    # The search lock is free: another search can start while the analysis runs.
    second, _ = await asyncio.wait_for(_search(searches), 2)
    assert second != sid

    analyzer.gate.set()
    done = await _finish(analysis, sid)
    assert done.state == "done"
    with Session(engine) as s:
        assert latest_analysis(s, sid) is not None
    await analysis.shutdown()


async def test_auto_analyze_skipped_when_disabled(engine: Engine) -> None:
    analyzer = FakeAnalyzer()
    analysis = _manager(engine, analyzer, auto=False)
    searches = SearchManager(engine, lambda q: QuickSource(ADS), None, analysis=analysis)
    sid, _ = await _search(searches)
    assert analysis.status(sid) is None
    assert analyzer.calls == 0


@pytest.mark.parametrize("case", ["analyzer_error", "no_relevant", "unavailable"])
async def test_auto_analyze_failure_does_not_fail_search(engine: Engine, case: str) -> None:
    analyzer = None if case == "unavailable" else FakeAnalyzer(fail="boom")
    ads = OFF_TOPIC if case == "no_relevant" else ADS
    analysis = _manager(engine, analyzer, auto=True)
    searches = SearchManager(engine, lambda q: QuickSource(ads), None, analysis=analysis)
    sid, events = await _search(searches)
    assert events[-1].kind == "done" and events[-1].message is None
    if case == "analyzer_error":
        job = await _finish(analysis, sid)
        assert job.state == "error" and job.message == "boom"
    else:
        assert analysis.status(sid) is None
    with Session(engine) as s:
        assert latest_analysis(s, sid) is None


async def test_manual_click_joining_an_auto_job_is_not_capped(
    engine: Engine, tmp_path: Path
) -> None:
    from adscout.services.analysis import AutoAnalysisCap

    sid = _seed(engine, ADS)
    analyzer = FakeAnalyzer()
    limit = [1]
    cap = AutoAnalysisCap(tmp_path / "data", lambda: limit[0], lambda: NOW.date())
    manager = AnalysisManager(
        engine, lambda: analyzer, auto_enabled=lambda: True, now=lambda: NOW, cap=cap
    )
    await manager.start_after_search(sid)  # the auto job is registered, not yet running
    limit[0] = 0  # the day's automatic budget runs out before it calls Claude
    joined = await manager.start(sid, force=False)  # the user clicks Analyze meanwhile
    assert joined.state == "running"
    done = await _finish(manager, sid)
    # The click asked for this analysis: the cap of unattended runs doesn't apply.
    assert done.state == "done"
    assert analyzer.calls == 1
    assert cap.used_today() == 0


async def test_auto_job_alone_still_respects_the_cap(engine: Engine, tmp_path: Path) -> None:
    from adscout.services.analysis import AutoAnalysisCap

    sid = _seed(engine, ADS)
    analyzer = FakeAnalyzer()
    limit = [1]
    cap = AutoAnalysisCap(tmp_path / "data", lambda: limit[0], lambda: NOW.date())
    manager = AnalysisManager(
        engine, lambda: analyzer, auto_enabled=lambda: True, now=lambda: NOW, cap=cap
    )
    await manager.start_after_search(sid)
    limit[0] = 0
    for _ in range(100):
        await asyncio.sleep(0.01)
        if manager.status(sid) is None:
            break
    assert manager.status(sid) is None and analyzer.calls == 0
