"""WatchScheduler: run the watchlist once a day inside ``adscout serve``.

An APScheduler cron job fires at the ``daily_check_time`` setting in the display timezone.
At startup a run missed by less than 24 hours (the app was closed at the scheduled time)
is caught up once; a job missed while the computer slept still runs on waking (misfire
grace just under the catch-up window). A run skipped because the browser profile is busy
is retried every 20 minutes while still inside that window. Only ``serve`` builds a
scheduler, after it holds the server lock.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

from adscout.config import DEFAULT_DAILY_CHECK_TIME, Settings, parse_hhmm
from adscout.watch.runner import RunSummary, WatchRunRecord, read_last_run

logger = logging.getLogger(__name__)

DAILY_JOB = "watch-daily"
CATCH_UP_JOB = "watch-catch-up"
RETRY_JOB = "watch-retry"
CATCH_UP_WINDOW = timedelta(hours=24)
# Waking from sleep within ~23 h of the slot still runs the day's check; ``coalesce`` and
# the runner's day record keep it to one run.
MISFIRE_GRACE_S = int((CATCH_UP_WINDOW - timedelta(hours=1)).total_seconds())
RETRY_DELAY = timedelta(minutes=20)


class Runner(Protocol):
    async def has_enabled_items(self) -> bool: ...

    async def run_all(self, now: datetime) -> RunSummary: ...


def daily_time(value: str) -> tuple[int, int]:
    """The configured "HH:MM", or the default when it is invalid (e.g. a bad ``.env``)."""
    try:
        return parse_hhmm(value)
    except ValueError:
        logger.warning("Invalid daily_check_time %r; using %s", value, DEFAULT_DAILY_CHECK_TIME)
        return parse_hhmm(DEFAULT_DAILY_CHECK_TIME)


def last_slot(now: datetime, at: tuple[int, int], tz: ZoneInfo) -> datetime:
    """The latest scheduled time at or before ``now`` (aware, in ``tz``)."""
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), time(*at), tzinfo=tz)
    if slot > local:
        slot = datetime.combine(local.date() - timedelta(days=1), time(*at), tzinfo=tz)
    return slot


def catch_up_due(
    now: datetime, at: tuple[int, int], tz: ZoneInfo, last: WatchRunRecord | None
) -> bool:
    """Whether the latest slot was missed less than 24 h ago and no run has covered it.

    Nothing runs when a run is recorded for today's local date or started after the slot.
    The run record is keyed by execution date on purpose (never two scrapes in one day), so
    a catch-up that runs before today's slot replaces today's check: the cron job then sees
    today's record and skips.
    """
    # In UTC: arithmetic between datetimes sharing a tzinfo ignores DST (wall-clock time).
    slot = last_slot(now, at, tz).astimezone(UTC)
    if now.astimezone(UTC) - slot >= CATCH_UP_WINDOW:
        return False
    if last is None:
        return True
    return last.date != now.astimezone(tz).date() and last.started_at.astimezone(UTC) < slot


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WatchScheduler:
    def __init__(
        self,
        runner: Runner,
        settings_provider: Callable[[], Settings],
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.runner = runner
        self._settings = settings_provider
        self._clock = clock
        self._scheduler: AsyncIOScheduler | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._config: tuple[tuple[int, int], str] | None = None

    def _current(self) -> tuple[tuple[int, int], ZoneInfo]:
        settings = self._settings()
        return daily_time(settings.daily_check_time), ZoneInfo(settings.timezone)

    def _trigger(self) -> CronTrigger:
        at, tz = self._current()
        self._config = (at, tz.key)
        return CronTrigger(hour=at[0], minute=at[1], timezone=tz)

    async def start(self) -> None:
        """Register the daily job (in the running loop) and catch up a missed run."""
        scheduler = AsyncIOScheduler(event_loop=asyncio.get_running_loop())
        scheduler.add_job(
            self.run_daily,
            trigger=self._trigger(),
            id=DAILY_JOB,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=MISFIRE_GRACE_S,
        )
        scheduler.start()
        self._scheduler = scheduler
        at, tz = self._current()
        items = await self.runner.has_enabled_items()
        logger.info(
            "Watch scheduler: daily check at %02d:%02d (%s), next %s; %s",
            at[0],
            at[1],
            tz.key,
            self.next_run_time(),
            "watch items enabled" if items else "no enabled watch items",
        )
        now = self._clock()
        if items and catch_up_due(now, at, tz, read_last_run(self._settings().data_dir)):
            logger.info("Watch scheduler: catching up the missed daily check now")
            scheduler.add_job(self.run_daily, id=CATCH_UP_JOB, max_instances=1)

    def reschedule(self) -> None:
        """Apply a changed check time or timezone (call after the settings change)."""
        if self._scheduler is None:
            return
        at, tz = self._current()
        if self._config == (at, tz.key):
            return
        self._scheduler.reschedule_job(DAILY_JOB, trigger=self._trigger())
        logger.info("Watch scheduler: daily check moved to %02d:%02d (%s)", at[0], at[1], tz.key)

    async def run_daily(self) -> None:
        """The job body: one daily run (the runner refuses a second run on the same day)."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            now = self._clock()
            summary = await self.runner.run_all(now)
            if summary.skipped == "profile_busy":
                self._schedule_retry(now)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The daily watch check failed")
        finally:
            if task is not None:
                self._tasks.discard(task)

    def _schedule_retry(self, now: datetime) -> None:
        """Try the skipped daily run again in 20 minutes, while that is still inside the
        catch-up window of the slot that came due (after it, the next slot's job takes over).
        """
        if self._scheduler is None:
            return
        at, tz = self._current()
        when = now + RETRY_DELAY
        # In UTC: arithmetic between datetimes sharing a tzinfo ignores DST (wall-clock time).
        slot = last_slot(now, at, tz).astimezone(UTC)
        if when.astimezone(UTC) - slot >= CATCH_UP_WINDOW:
            logger.info("Watch scheduler: browser still busy; no retry left today")
            return
        self._scheduler.add_job(
            self.run_daily,
            trigger="date",
            run_date=when,
            id=RETRY_JOB,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=MISFIRE_GRACE_S,
        )
        logger.info("Watch scheduler: the browser is busy; retrying the daily check at %s", when)

    def retry_run_time(self) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job(RETRY_JOB)
        when = getattr(job, "next_run_time", None) if job is not None else None
        return when if isinstance(when, datetime) else None

    async def shutdown(self) -> None:
        """Stop the scheduler and cancel a run in progress."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        tasks = [t for t in self._tasks if not t.done() and t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    # --- Introspection (tests, logs) ---------------------------------------------------------

    def job_ids(self) -> set[str]:
        if self._scheduler is None:
            return set()
        return {job.id for job in self._scheduler.get_jobs()}

    def daily_job(self) -> Any:
        assert self._scheduler is not None
        return self._scheduler.get_job(DAILY_JOB)

    def next_run_time(self) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job(DAILY_JOB)
        when = getattr(job, "next_run_time", None) if job is not None else None
        return when if isinstance(when, datetime) else None
