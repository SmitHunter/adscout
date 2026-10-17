import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.analysis.backends import UNAVAILABLE, AnalysisError, Analyzer, BackendName
from adscout.analysis.models import AngleAnalysis
from adscout.api.app import ALLOWED_HOSTS, create_app
from adscout.config import Settings
from adscout.db.repo import create_snapshot, finish_snapshot, make_engine
from adscout.models import Ad, SearchQuery
from adscout.services.analysis_jobs import AnalysisManager, AnalysisUnavailable
from adscout.services.searches import SearchManager
from adscout.sources import CancelToken, ProgressCallback, SearchResult
from adscout.sources.base import emit_progress
from adscout.watch.runner import ItemRun, RunSummary

NOW = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
# The in-process client's host: allowed only in tests, never in production.
TEST_HOSTS = [*ALLOWED_HOSTS, "test"]
KEYWORD = "posture corrector"


def make_ad(ad_id: str, page: str, month: int, body: str | None = None) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=f"pid-{page.split()[0].lower()}",
        page_name=page,
        start_date=datetime(2026, month, 5, 3, 0, tzinfo=UTC),
        is_active=True,
        body=body if body is not None else f"Best posture corrector, offer {ad_id}",
    )


ADS = [
    make_ad("1", "Alpha Store", 3),
    make_ad("2", "Alpha Store", 5),
    make_ad("3", "Beta Shop", 8),
    make_ad("4", "Gamma Co", 10, body="Yoga mats on sale"),
]


def seed(engine: Engine, keyword: str = KEYWORD, status: str = "ok") -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=keyword, country="US"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ADS, "status": status}))
        return snap.id


class GatedSource:
    """Emits progress, then waits for ``gate`` (or a cancel) before returning."""

    name = "fake"

    def __init__(self) -> None:
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
        ads = ADS[:2]
        await emit_progress(progress, ads, "scrolling", "2 ads so far")
        self.started.set()
        assert cancel is not None
        gate = asyncio.ensure_future(self.gate.wait())
        stop = asyncio.ensure_future(cancel.wait())
        await asyncio.wait({gate, stop}, return_when=asyncio.FIRST_COMPLETED)
        gate.cancel()
        stop.cancel()
        if cancel.cancelled:
            return SearchResult(ads=ads, status="cancelled", message="Search cancelled.")
        return SearchResult(ads=ads, status="ok")


ANALYSIS_RESULT: dict[str, Any] = {
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
    """Never calls Claude. ``gate`` holds the analysis open; ``fail`` raises AnalysisError."""

    def __init__(self) -> None:
        self.name: BackendName = "cli"
        self.model = "claude-sonnet-5-5"
        self.calls = 0
        self.available = True
        self.auto = False  # the auto_analyze setting
        self.gate: asyncio.Event | None = None
        self.fail: str | None = None

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise AnalysisError(self.fail)
        return AngleAnalysis.model_validate(ANALYSIS_RESULT), None


class FakeWatchRunner:
    """Records "Run now" calls; ``gate`` holds a run open. Never searches."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def has_enabled_items(self) -> bool:
        return False

    async def run_all(self, now: datetime) -> RunSummary:
        return RunSummary(started_at=now, finished_at=now, skipped="no_items")

    async def run_one(self, item_id: int) -> ItemRun:
        self.calls.append(item_id)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return ItemRun(watch_item_id=item_id, query="x", snapshot_id=None, status="ok")


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class FakeRenderer:
    """Stands in for headless Chromium: returns fixed bytes, or raises ``fail``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.fail: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def __call__(self, base_url: str, snapshot_id: int, fmt: str) -> bytes:
        self.calls.append((base_url, snapshot_id, fmt))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return b"%PDF-1.7 fake" if fmt == "pdf" else PNG_MAGIC + b" fake"


@dataclass
class Env:
    app: FastAPI
    client: httpx.AsyncClient
    engine: Engine
    settings: Settings
    manager: SearchManager
    source: GatedSource
    static_dir: Path
    analyzer: FakeAnalyzer
    analysis: AnalysisManager
    opened: list[str] = field(default_factory=list)
    runner: FakeWatchRunner = field(default_factory=FakeWatchRunner)
    renderer: FakeRenderer = field(default_factory=FakeRenderer)


async def wait_idle(manager: SearchManager) -> None:
    for _ in range(300):
        if manager.active() is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("search did not finish")


def _unsupported(q: SearchQuery, source: GatedSource) -> GatedSource:
    if q.country == "EU":
        raise ValueError("EU searches are only available through the official API.")
    return source


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    d = tmp_path / "static"
    d.mkdir()
    return d


@pytest.fixture
async def env(tmp_path: Path, static_dir: Path) -> AsyncIterator[Env]:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    engine = make_engine(settings.db_path)
    source = GatedSource()
    opened: list[str] = []
    analyzer = FakeAnalyzer()
    runner = FakeWatchRunner()
    renderer = FakeRenderer()

    def analyzer_factory() -> Analyzer:
        if not analyzer.available:
            raise AnalysisUnavailable(UNAVAILABLE)
        return analyzer

    analysis = AnalysisManager(
        engine, analyzer_factory, auto_enabled=lambda: analyzer.auto, now=lambda: NOW
    )
    manager = SearchManager(engine, lambda q: _unsupported(q, source), None, analysis=analysis)
    app = create_app(
        settings,
        engine=engine,
        manager=manager,
        analysis=analysis,
        static_dir=static_dir,
        open_browser=lambda: opened.append("opened"),
        now=lambda: NOW,
        watch_runner=runner,
        render_report=renderer,
        allowed_hosts=TEST_HOSTS,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(
                app,
                client,
                engine,
                settings,
                manager,
                source,
                static_dir,
                analyzer,
                analysis,
                opened,
                runner,
                renderer,
            )
