"""AnalysisManager: run angle analyses as background jobs in the server loop.

One job per snapshot at a time. A finished analysis lives in the DB, so its status survives
restarts; a failed job is kept in memory (with the user-facing message) until a newer
analysis exists, or for an hour at most (finished jobs are pruned after that).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.analysis.backends import AnalysisError, Analyzer
from adscout.db.repo import latest_analysis
from adscout.db.tables import Analysis, Snapshot
from adscout.services.analysis import (
    AutoAnalysisCap,
    AutoCapReached,
    NoRelevantAds,
    analysis_input,
    run_analysis,
)

logger = logging.getLogger(__name__)

INTERRUPTED = "Analysis interrupted."
FAILED = "Angle analysis failed. Check the server log for details."

JobState = Literal["running", "done", "error"]

JOB_RETENTION = timedelta(hours=1)


class AnalysisJob(BaseModel):
    snapshot_id: int
    state: JobState
    message: str | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    model: str | None
    backend: str | None
    creatives: int


class AnalysisUnavailable(Exception):
    """No Claude backend can run. ``message`` is the user-facing setup instruction."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


AnalyzerFactory = Callable[[], Analyzer]
"""Returns the analyzer for the current settings, or raises :class:`AnalysisUnavailable`."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def job_from_analysis(row: Analysis) -> AnalysisJob:
    return AnalysisJob(
        snapshot_id=row.snapshot_id,
        state="done",
        started_at=row.created_at,
        finished_at=row.created_at,
        model=row.model,
        backend=row.backend,
        creatives=row.ad_count,
    )


@dataclass
class _Entry:
    job: AnalysisJob
    task: asyncio.Task[None] | None = None
    # Unattended (auto-analyze): counts against the daily cap. A manual start that joins
    # the running job clears it, so the user's click is never skipped by the cap.
    auto: bool = False


class AnalysisManager:
    """Starts analyses, reports their status and stops them on shutdown."""

    def __init__(
        self,
        engine: Engine,
        analyzer_factory: AnalyzerFactory,
        *,
        auto_enabled: Callable[[], bool] = lambda: False,
        now: Callable[[], datetime] = _utcnow,
        cap: AutoAnalysisCap | None = None,
    ) -> None:
        self._engine = engine
        self._factory = analyzer_factory
        self._auto_enabled = auto_enabled
        self._now = now
        self._cap = cap
        self._jobs: dict[int, _Entry] = {}

    async def start(self, snapshot_id: int, force: bool) -> AnalysisJob:
        """Start a manual analysis of ``snapshot_id`` (or return its running job).

        Raises ``LookupError`` (unknown snapshot), :class:`NoRelevantAds` or
        :class:`AnalysisUnavailable` before any job starts.
        """
        return await self._start(snapshot_id, force, auto=False)

    async def _start(self, snapshot_id: int, force: bool, *, auto: bool) -> AnalysisJob:
        self._prune()
        entry = self._jobs.get(snapshot_id)
        if entry is not None and entry.job.state == "running":
            return self._join(entry, auto)
        items = await asyncio.to_thread(self._input_count, snapshot_id)
        # Another start may have registered a job while the DB was read.
        entry = self._jobs.get(snapshot_id)
        if entry is not None and entry.job.state == "running":
            return self._join(entry, auto)
        if not items:
            raise NoRelevantAds
        analyzer = self._factory()
        job = AnalysisJob(
            snapshot_id=snapshot_id,
            state="running",
            started_at=self._now(),
            model=analyzer.model,
            backend=analyzer.name,
            creatives=items,
        )
        entry = _Entry(job, auto=auto)
        self._jobs[snapshot_id] = entry
        entry.task = asyncio.create_task(self._execute(entry, analyzer, force, auto))
        return job

    @staticmethod
    def _join(entry: _Entry, auto: bool) -> AnalysisJob:
        if not auto:
            entry.auto = False
        return entry.job

    def _input_count(self, snapshot_id: int) -> int:
        with Session(self._engine) as session:
            _, items = analysis_input(session, snapshot_id)
        return len(items)

    def _prune(self) -> None:
        """Forget finished jobs older than an hour (their results live in the DB)."""
        cutoff = self._now() - JOB_RETENTION
        stale = [
            sid
            for sid, e in self._jobs.items()
            if e.job.state != "running"
            and e.job.finished_at is not None
            and e.job.finished_at < cutoff
        ]
        for sid in stale:
            del self._jobs[sid]

    def status(self, snapshot_id: int) -> AnalysisJob | None:
        """The running or failed job, else the latest stored analysis as ``done``, else None.

        Raises ``LookupError`` for an unknown snapshot.
        """
        entry = self._jobs.get(snapshot_id)
        with Session(self._engine) as session:
            if session.get(Snapshot, snapshot_id) is None:
                raise LookupError(f"snapshot {snapshot_id} not found")
            row = latest_analysis(session, snapshot_id)
        job = entry.job if entry is not None else None
        if job is not None and job.state == "running":
            return job
        # A failure stays visible until a newer analysis (e.g. from the CLI) replaces it.
        newer = row is not None and (job is None or row.created_at >= job.started_at)
        if job is not None and job.state == "error" and not newer:
            return job
        return job_from_analysis(row) if row is not None else None

    def auto_enabled(self) -> bool:
        """Whether a search should be followed by an analysis (setting on, backend available)."""
        try:
            return self._auto_enabled()
        except Exception:
            logger.exception("Could not read the auto-analyze setting")
            return False

    async def start_after_search(self, snapshot_id: int) -> None:
        """Start a detached analysis for a just-finished search when auto-analyze is on.

        Counts against the daily cap of unattended analyses (only when Claude is really
        called: a cached or reused result is free). Never raises: problems starting are
        logged and skipped; problems while running are recorded on the job.
        """
        if not self.auto_enabled():
            return
        if self._cap is not None and self._cap.exhausted():
            logger.info(
                "Auto-analyze skipped for snapshot %s: the daily limit of automatic "
                "analyses is reached",
                snapshot_id,
            )
            return
        try:
            await self._start(snapshot_id, force=False, auto=True)
        except NoRelevantAds:
            logger.info("Auto-analyze skipped for snapshot %s: no relevant ads", snapshot_id)
        except AnalysisUnavailable as exc:
            logger.info("Auto-analyze skipped for snapshot %s: %s", snapshot_id, exc.message)
        except Exception:
            logger.exception("Auto-analyze failed to start for snapshot %s", snapshot_id)

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel every running analysis and wait for them to stop."""
        tasks = [e.task for e in self._jobs.values() if e.task is not None and not e.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        for entry in self._jobs.values():
            # A task cancelled before it first ran never reached its own handler.
            if entry.job.state == "running" and entry.task is not None and entry.task.done():
                entry.job = entry.job.model_copy(
                    update={"state": "error", "message": INTERRUPTED, "finished_at": self._now()}
                )

    async def wait_idle(self) -> None:
        """Wait for every running analysis to finish (the CLI, before it exits)."""
        tasks = [e.task for e in self._jobs.values() if e.task is not None and not e.task.done()]
        if tasks:
            await asyncio.wait(tasks)

    async def _execute(self, entry: _Entry, analyzer: Analyzer, force: bool, auto: bool) -> None:
        job = entry.job
        update: dict[str, object]
        cap = self._cap

        def allow() -> bool:
            # Read when Claude is about to be called: a manual click may have joined.
            return not entry.auto or cap is None or cap.take()

        try:
            row = await run_analysis(
                self._engine,
                job.snapshot_id,
                analyzer,
                self._now(),
                force,
                allow_call=allow if auto else None,
            )
            update = {"state": "done", "creatives": row.ad_count}
        except AutoCapReached:
            logger.info(
                "Auto-analyze skipped for snapshot %s: the daily limit of automatic "
                "analyses is reached",
                job.snapshot_id,
            )
            if self._jobs.get(job.snapshot_id) is entry:
                del self._jobs[job.snapshot_id]
            return
        except asyncio.CancelledError:
            entry.job = job.model_copy(
                update={"state": "error", "message": INTERRUPTED, "finished_at": self._now()}
            )
            raise
        except AnalysisError as exc:
            update = {"state": "error", "message": exc.message}
        except ValueError as exc:
            # NoRelevantAds if relevance changed after the job started.
            update = {"state": "error", "message": str(exc)}
        except Exception:
            logger.exception("Angle analysis failed for snapshot %s", job.snapshot_id)
            update = {"state": "error", "message": FAILED}
        entry.job = job.model_copy(update={**update, "finished_at": self._now()})
