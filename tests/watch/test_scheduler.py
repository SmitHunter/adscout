"""WatchScheduler: the daily cron job, the startup catch-up and rescheduling."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from adscout.config import Settings
from adscout.watch.runner import RunSummary, WatchRunRecord, write_last_run
from adscout.watch.scheduler import (
    CATCH_UP_JOB,
    CATCH_UP_WINDOW,
    DAILY_JOB,
    RETRY_JOB,
    WatchScheduler,
    catch_up_due,
)

MEL = ZoneInfo("Australia/Melbourne")


class FakeRunner:
    def __init__(self, items: bool = True, skipped: str | None = None) -> None:
        self.calls: list[datetime] = []
        self.items = items
        self.skipped = skipped

    async def has_enabled_items(self) -> bool:
        return self.items

    async def run_all(self, now: datetime) -> RunSummary:
        self.calls.append(now)
        return RunSummary.model_validate(
            {"started_at": now, "finished_at": now, "skipped": self.skipped}
        )


def _settings(tmp_path: Path, at: list[str]) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        timezone="Australia/Melbourne",
        daily_check_time=at[0],
    )


def _local(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=MEL)


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


def test_catch_up_rule() -> None:
    nine = (9, 0)
    # Missed this morning's 09:00 (app opened at 15:00), nothing recorded: catch up.
    assert catch_up_due(_local(2026, 10, 15, 15, 0), nine, MEL, None)
    # Before today's slot: yesterday's 09:00 was missed 23 h ago: catch up.
    assert catch_up_due(_local(2026, 10, 15, 8, 0), nine, MEL, None)
    # Already ran today, or after the slot: nothing to catch up.
    ran = WatchRunRecord(
        date=date(2026, 10, 15),
        started_at=_local(2026, 10, 15, 9, 0),
        finished_at=_local(2026, 10, 15, 9, 5),
    )
    assert not catch_up_due(_local(2026, 10, 15, 15, 0), nine, MEL, ran)
    yesterday = ran.model_copy(update={"date": date(2026, 10, 14)})
    assert not catch_up_due(
        _local(2026, 10, 15, 8, 0),
        nine,
        MEL,
        yesterday.model_copy(update={"started_at": _local(2026, 10, 14, 9, 1)}),
    )


async def test_catch_up_on_startup_within_24h(tmp_path: Path) -> None:
    runner = FakeRunner()
    now = _local(2026, 10, 15, 15, 0)
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    try:
        await _settle()
        assert runner.calls == [now]
        assert sched.job_ids() == {DAILY_JOB}  # the one-off catch-up has run
    finally:
        await sched.shutdown()


async def test_no_catch_up_after_24h(tmp_path: Path) -> None:
    # Daylight saving ends in Melbourne on 5 April 2026: that day is 25 hours long, so at
    # 08:30 the last 09:00 slot (4 April) is 24.5 h old: too old to catch up.
    assert not catch_up_due(_local(2026, 4, 5, 8, 30), (9, 0), MEL, None)
    runner = FakeRunner()
    now = _local(2026, 4, 5, 8, 30)
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    try:
        await _settle()
        assert runner.calls == []
        assert CATCH_UP_JOB not in sched.job_ids()
    finally:
        await sched.shutdown()


async def test_no_catch_up_when_already_ran_or_no_items(tmp_path: Path) -> None:
    now = _local(2026, 10, 15, 15, 0)
    write_last_run(
        tmp_path / "data",
        WatchRunRecord(
            date=date(2026, 10, 15), started_at=now - timedelta(hours=6), finished_at=None
        ),
    )
    runner = FakeRunner()
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    await _settle()
    await sched.shutdown()
    assert runner.calls == []

    idle = FakeRunner(items=False)
    sched = WatchScheduler(idle, lambda: _settings(tmp_path / "b", ["09:00"]), lambda: now)
    await sched.start()
    await _settle()
    await sched.shutdown()
    assert idle.calls == []


async def test_daily_job_registered_with_polite_options(tmp_path: Path) -> None:
    runner = FakeRunner(items=False)
    now = _local(2026, 10, 15, 8, 0)
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    try:
        job = sched.daily_job()
        assert job.coalesce is True and job.max_instances == 1
        # Waking from sleep up to ~23 h late still runs the day's check (coalesced to one).
        assert job.misfire_grace_time == (CATCH_UP_WINDOW - timedelta(hours=1)).total_seconds()
        assert job.misfire_grace_time == 23 * 3600
        nxt = sched.next_run_time()
        assert nxt is not None
        local = nxt.astimezone(MEL)
        assert (local.hour, local.minute) == (9, 0)
    finally:
        await sched.shutdown()


async def test_reschedule_on_time_change(tmp_path: Path) -> None:
    runner = FakeRunner(items=False)
    at = ["09:00"]
    now = _local(2026, 10, 15, 8, 0)
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, at), lambda: now)
    await sched.start()
    try:
        at[0] = "18:30"
        sched.reschedule()
        nxt = sched.next_run_time()
        assert nxt is not None
        local = nxt.astimezone(MEL)
        assert (local.hour, local.minute) == (18, 30)
        assert runner.calls == []  # rescheduling never runs anything by itself
    finally:
        await sched.shutdown()
    assert sched.job_ids() == set()


async def test_job_runs_runner_with_clock(tmp_path: Path) -> None:
    runner = FakeRunner(items=False)
    now = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    try:
        await sched.run_daily()
        assert runner.calls == [now]
    finally:
        await sched.shutdown()


# Retry dates are in the future so the real scheduler never fires them during a test.


async def test_profile_busy_at_start_schedules_retry(tmp_path: Path) -> None:
    runner = FakeRunner(skipped="profile_busy")
    now = _local(2030, 10, 15, 15, 0)  # missed today's 09:00: the catch-up runs at start
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    try:
        await _settle()
        assert runner.calls == [now]
        assert sched.job_ids() == {DAILY_JOB, RETRY_JOB}
        assert sched.retry_run_time() == now + timedelta(minutes=20)
    finally:
        await sched.shutdown()


async def test_no_retry_after_a_run_that_was_not_profile_busy(tmp_path: Path) -> None:
    now = _local(2030, 10, 15, 15, 0)
    for skipped in (None, "already_ran", "no_items", "busy"):
        runner = FakeRunner(items=False, skipped=skipped)
        sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
        await sched.start()
        try:
            await sched.run_daily()
            assert sched.job_ids() == {DAILY_JOB}, skipped
        finally:
            await sched.shutdown()


async def test_retry_outside_catch_up_window_not_scheduled(tmp_path: Path) -> None:
    runner = FakeRunner(items=False, skipped="profile_busy")
    # 23 h 50 min after yesterday's 09:00: a retry in 20 min would be past the window.
    now = _local(2030, 10, 16, 8, 50)
    sched = WatchScheduler(runner, lambda: _settings(tmp_path, ["09:00"]), lambda: now)
    await sched.start()
    try:
        await sched.run_daily()
        assert runner.calls == [now]
        assert sched.job_ids() == {DAILY_JOB}
        assert sched.retry_run_time() is None
    finally:
        await sched.shutdown()


@pytest.mark.parametrize("bad", ["", "25:00"])
def test_bad_time_falls_back_to_default(tmp_path: Path, bad: str) -> None:
    from adscout.watch.scheduler import daily_time

    assert daily_time(bad) == (9, 0)
