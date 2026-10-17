"""Shared server state and the FastAPI dependencies that hand it to routes."""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine

from adscout.analysis.backends import UNAVAILABLE, Analyzer, analysis_status, resolve_analyzer
from adscout.config import Settings
from adscout.demo import DEMO_COPY
from adscout.services.analysis import AutoAnalysisCap
from adscout.services.analysis_jobs import AnalysisManager, AnalysisUnavailable
from adscout.services.export import ReportRenderer
from adscout.services.media import MediaDownloader
from adscout.services.searches import AnalysisStep, SearchManager
from adscout.settings_store import effective_settings
from adscout.sources.select import source_factory

if TYPE_CHECKING:
    from adscout.watch.runner import ItemRun, RunSummary
    from adscout.watch.scheduler import WatchScheduler

BrowserOpener = Callable[[], None]


class WatchRunnerLike(Protocol):
    """What the API and the scheduler need from ``WatchRunner`` (tests pass a fake)."""

    async def has_enabled_items(self) -> bool: ...

    async def run_all(self, now: datetime) -> "RunSummary": ...

    async def run_one(self, item_id: int) -> "ItemRun": ...


@dataclass
class ApiState:
    settings: Settings
    engine: Engine
    manager: SearchManager
    analysis: AnalysisManager
    open_browser: BrowserOpener
    now: Callable[[], datetime]
    watch_runner: WatchRunnerLike
    render_report: ReportRenderer
    # Where the export browser opens the print page: the server's own bound address.
    export_base_url: str
    browser_thread: threading.Thread | None = None
    # The daily watchlist scheduler: only ``adscout serve`` (holding the server lock) has one.
    scheduler: "WatchScheduler | None" = None
    # "Run now" checks in progress, by watch item id.
    watch_tasks: dict[int, "asyncio.Task[Any]"] = field(default_factory=dict)
    # One export browser at a time: a second concurrent export gets a 409.
    export_slot: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    # ``adscout demo``: synthetic data; searches, checks, analyses and the browser are off.
    demo: bool = False

    def browser_open(self) -> bool:
        return self.browser_thread is not None and self.browser_thread.is_alive()


def build_manager(
    settings: Settings, engine: Engine, analysis: AnalysisStep | None = None
) -> SearchManager:
    """The real manager: source picked per query, media downloaded (and, when the user turned
    auto-analyze on, angles analysed) after each search."""
    return SearchManager(
        engine,
        source_factory(settings),
        MediaDownloader(settings.media_dir),
        analysis=analysis,
    )


def build_analysis_manager(
    settings: Settings, engine: Engine, now: Callable[[], datetime]
) -> AnalysisManager:
    """The real analysis manager. Settings are re-read on every call, so a change on the
    Settings screen applies to the next analysis without a restart."""

    def analyzer() -> Analyzer:
        current = effective_settings(settings)
        found = resolve_analyzer(current)
        if found is None:
            raise AnalysisUnavailable(analysis_status(current).reason or UNAVAILABLE)
        return found

    def auto_enabled() -> bool:
        current = effective_settings(settings)
        return current.auto_analyze and analysis_status(current).available

    def today() -> date:
        return now().astimezone(ZoneInfo(effective_settings(settings).timezone)).date()

    cap = AutoAnalysisCap(
        settings.data_dir, lambda: effective_settings(settings).max_auto_analyses_per_day, today
    )
    return AnalysisManager(engine, analyzer, auto_enabled=auto_enabled, now=now, cap=cap)


def build_watch_runner(
    settings: Settings,
    engine: Engine,
    manager: SearchManager,
    now: Callable[[], datetime],
    browser_open: Callable[[], bool] = lambda: False,
) -> WatchRunnerLike:
    """The real watch runner over the server's own search manager. Shared by "Run now" and
    the daily scheduler, so both go through one search queue. ``browser_open`` tells it the
    AdScout browser window is open (a daily run then waits for a retry)."""
    from adscout.watch.runner import WatchRunner

    return WatchRunner(
        engine,
        manager,
        settings=lambda: effective_settings(settings),
        clock=now,
        browser_open=browser_open,
    )


def build_watch_scheduler(state: ApiState) -> "WatchScheduler":
    """The daily watchlist scheduler over the app's watch runner (``serve`` only)."""
    from adscout.watch.scheduler import WatchScheduler

    return WatchScheduler(state.watch_runner, lambda: effective_settings(state.settings), state.now)


WatchSchedulerFactory = Callable[[ApiState], "WatchScheduler"]


def get_state(request: Request) -> ApiState:
    return cast(ApiState, request.app.state.api)


State = Annotated[ApiState, Depends(get_state)]


def refuse_in_demo(state: ApiState) -> None:
    """Raise 403 with the demo copy when the app serves demo data (no live actions)."""
    if state.demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_COPY)
