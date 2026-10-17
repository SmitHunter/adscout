"""WatchRunner: one item at a time, polite gaps, alerts, block handling, once a day."""

import asyncio
import json
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.config import Settings
from adscout.db.repo import (
    create_watch_item,
    delete_watch_item,
    get_watch_item,
    list_alerts,
    list_snapshots,
    make_engine,
    snapshots_for_watch,
    update_watch_item,
)
from adscout.locks import ProfileBusy, acquire_profile, profile_lock, profile_lock_dir
from adscout.models import Ad, SearchQuery
from adscout.services.searches import SearchManager
from adscout.sources import CancelToken, ProgressCallback, SearchResult
from adscout.watch import runner as runner_module
from adscout.watch.rules import BLOCKED_MESSAGE
from adscout.watch.runner import WATCH_RUNS_FILE, WatchRunner, read_last_run

DAY1 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)


def _ads(product: str, count: int) -> list[Ad]:
    return [
        Ad(
            source="fake",
            ad_archive_id=f"{product[:3]}-{count}-{n}",
            page_id=f"pid-{n % 4}",
            page_name=f"Shop {n % 4}",
            start_date=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=n),
            is_active=True,
            body=f"Best {product}, variant {n}",
        )
        for n in range(count)
    ]


class Market:
    """A fake Meta: per keyword, a queue of (status, ad count) answers."""

    def __init__(self, manager_ref: list[SearchManager]) -> None:
        self.answers: dict[str, list[tuple[str, int]]] = {}
        self.searched: list[str] = []
        self.queued_at_start: list[int] = []
        self.on_search: Callable[[SearchQuery], None] | None = None
        self._manager = manager_ref

    def __call__(self, q: SearchQuery) -> "MarketSource":
        return MarketSource(self)


class MarketSource:
    name = "fake"

    def __init__(self, market: Market) -> None:
        self.market = market

    def supports(self, q: SearchQuery) -> bool:
        return True

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        self.market.searched.append(q.keyword)
        if self.market.on_search is not None:
            self.market.on_search(q)
        self.market.queued_at_start.append(self.market._manager[0].queue_size())
        answers = self.market.answers.get(q.keyword, [])
        status, count = answers.pop(0) if answers else ("ok", 6)
        if status == "profile_busy":
            raise ProfileBusy()
        if status == "blocked":
            return SearchResult(ads=[], status="blocked", message="Meta asked for a login.")
        return SearchResult(ads=_ads(q.keyword, count), status=status)


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.notes: list[tuple[str, str]] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def notify(self, title: str, body: str) -> None:
        self.notes.append((title, body))


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _settings(tmp_path: Path, notifications: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        timezone="UTC",
        desktop_notifications=notifications,
    )


def _setup(
    engine: Engine,
    tmp_path: Path,
    notifications: bool = True,
    browser_open: Callable[[], bool] = lambda: False,
) -> tuple[WatchRunner, Market, Recorder, list[datetime]]:
    ref: list[SearchManager] = []
    market = Market(ref)
    manager = SearchManager(engine, market, None)
    ref.append(manager)
    rec = Recorder()
    clock = [DAY1]
    runner = WatchRunner(
        engine,
        manager,
        settings=lambda: _settings(tmp_path, notifications),
        sleep=rec.sleep,
        rng=random.Random(7),
        notifier=rec.notify,
        clock=lambda: clock[0],
        browser_open=browser_open,
    )
    return runner, market, rec, clock


def _watch(engine: Engine, *keywords: str) -> list[int]:
    ids = []
    with Session(engine) as s:
        for k in keywords:
            item = create_watch_item(s, SearchQuery(keyword=k, country="US"))
            assert item.id is not None
            ids.append(item.id)
    return ids


async def test_run_all_sequential_with_gaps_in_range(engine: Engine, tmp_path: Path) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path)
    ids = _watch(engine, "posture corrector", "neck pillow", "ice roller", "lint remover")
    with Session(engine) as s:
        update_watch_item(s, ids[3], enabled=False)

    summary = await runner.run_all(DAY1)

    assert market.searched == ["posture corrector", "neck pillow", "ice roller"]
    assert market.queued_at_start == [0, 0, 0]  # never more than one search at a time
    assert len(rec.sleeps) == 2 and all(30 <= s <= 90 for s in rec.sleeps)
    assert [r.status for r in summary.items] == ["ok", "ok", "ok"]
    assert not summary.blocked and summary.skipped is None
    with Session(engine) as s:
        for item_id in ids[:3]:
            item = get_watch_item(s, item_id)
            assert item is not None and item.last_status == "ok"
            assert item.last_run_at == DAY1
            [snap] = snapshots_for_watch(s, item_id)
            assert snap.status == "ok"
        disabled = get_watch_item(s, ids[3])
        assert disabled is not None and disabled.last_run_at is None
    record = json.loads((tmp_path / "data" / WATCH_RUNS_FILE).read_text(encoding="utf-8"))
    assert record["date"] == "2026-10-15"
    assert record["started_at"] and record["finished_at"]


async def test_blocked_stops_and_alerts(engine: Engine, tmp_path: Path) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path)
    ids = _watch(engine, "posture corrector", "neck pillow", "ice roller")
    market.answers["neck pillow"] = [("blocked", 0)]

    summary = await runner.run_all(DAY1)

    assert market.searched == ["posture corrector", "neck pillow"]
    assert summary.blocked
    with Session(engine) as s:
        [alert] = list_alerts(s)
        assert alert.kind == "blocked" and alert.message == BLOCKED_MESSAGE
        assert alert.watch_item_id == ids[1]
        blocked = get_watch_item(s, ids[1])
        assert blocked is not None and blocked.last_status == "blocked"
        untouched = get_watch_item(s, ids[2])
        assert untouched is not None and untouched.last_run_at is None
    assert rec.notes == [("AdScout", BLOCKED_MESSAGE)]
    # The day counts as run: no retry today.
    assert (await runner.run_all(DAY1 + timedelta(hours=3))).skipped == "already_ran"


async def test_alert_created_and_notified(engine: Engine, tmp_path: Path) -> None:
    runner, market, rec, clock = _setup(engine, tmp_path)
    [item_id] = _watch(engine, "posture corrector")
    market.answers["posture corrector"] = [("ok", 10), ("ok", 20)]

    await runner.run_all(DAY1)
    with Session(engine) as s:
        assert list_alerts(s) == []  # first snapshot: nothing to compare with
    clock[0] = DAY2
    summary = await runner.run_all(DAY2)

    message = "posture corrector: active ads up 100% (10 → 20)"
    assert summary.items[0].alert == message
    with Session(engine) as s:
        [alert] = list_alerts(s)
        assert alert.kind == "change" and alert.message == message
        assert alert.change_pct == 100.0 and alert.watch_item_id == item_id
    assert rec.notes == [("AdScout", message)]


async def test_alert_not_notified_when_disabled(engine: Engine, tmp_path: Path) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path, notifications=False)
    _watch(engine, "posture corrector")
    market.answers["posture corrector"] = [("ok", 10), ("ok", 20)]
    await runner.run_all(DAY1)
    await runner.run_all(DAY2)
    with Session(engine) as s:
        assert len(list_alerts(s)) == 1
    assert rec.notes == []


async def test_never_twice_same_day(engine: Engine, tmp_path: Path) -> None:
    runner, market, _, _ = _setup(engine, tmp_path)
    _watch(engine, "posture corrector")

    first = await runner.run_all(DAY1)
    again = await runner.run_all(DAY1 + timedelta(hours=10))
    assert first.skipped is None and again.skipped == "already_ran"
    assert market.searched == ["posture corrector"]
    last = read_last_run(tmp_path / "data")
    assert last is not None and last.date.isoformat() == "2026-10-15"

    # A corrupt record is tolerated (reads as "never ran").
    (tmp_path / "data" / WATCH_RUNS_FILE).write_text("{oops", encoding="utf-8")
    assert read_last_run(tmp_path / "data") is None
    assert (await runner.run_all(DAY2)).skipped is None
    assert market.searched == ["posture corrector", "posture corrector"]


async def test_run_all_without_items_records_nothing(engine: Engine, tmp_path: Path) -> None:
    runner, market, _, _ = _setup(engine, tmp_path)
    summary = await runner.run_all(DAY1)
    assert summary.skipped == "no_items" and market.searched == []
    assert read_last_run(tmp_path / "data") is None


async def test_run_one_has_no_gap_and_ignores_day_record(engine: Engine, tmp_path: Path) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path)
    [item_id] = _watch(engine, "posture corrector")
    await runner.run_all(DAY1)
    result = await runner.run_one(item_id)
    assert result.status == "ok"
    assert market.searched == ["posture corrector", "posture corrector"]
    assert rec.sleeps == []
    with pytest.raises(LookupError):
        await runner.run_one(999)


async def test_item_deleted_during_its_check_raises_no_alert(
    engine: Engine, tmp_path: Path
) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path)
    [item_id] = _watch(engine, "posture corrector")
    market.answers["posture corrector"] = [("blocked", 0)]

    def delete_item(q: SearchQuery) -> None:
        with Session(engine) as s:
            assert delete_watch_item(s, item_id)

    market.on_search = delete_item
    result = await runner.run_one(item_id)

    assert result.status == "blocked" and result.alert is None
    with Session(engine) as s:
        assert list_alerts(s) == []
    assert rec.notes == []


# --- Unattended operation: a busy profile, stale item lists, hanging notifications --------


async def test_profile_busy_at_start_records_nothing(engine: Engine, tmp_path: Path) -> None:
    runner, market, _, _ = _setup(engine, tmp_path)
    _watch(engine, "posture corrector")
    settings = _settings(tmp_path)
    held = profile_lock(profile_lock_dir(settings.browser_profile_dir), timeout_s=0)
    acquire_profile(held)  # another process (a CLI scrape) is using the browser
    try:
        summary = await runner.run_all(DAY1)
    finally:
        held.release()

    assert summary.skipped == "profile_busy" and summary.items == []
    assert market.searched == []
    assert read_last_run(tmp_path / "data") is None  # the day's check is still owed
    # Once the profile is free the same day's check runs.
    assert (await runner.run_all(DAY1 + timedelta(minutes=20))).skipped is None
    assert market.searched == ["posture corrector"]


async def test_browser_window_open_at_start_records_nothing(engine: Engine, tmp_path: Path) -> None:
    window = [True]
    runner, market, _, _ = _setup(engine, tmp_path, browser_open=lambda: window[0])
    _watch(engine, "posture corrector")

    assert (await runner.run_all(DAY1)).skipped == "profile_busy"
    assert market.searched == [] and read_last_run(tmp_path / "data") is None
    window[0] = False
    assert (await runner.run_all(DAY1)).skipped is None


async def test_profile_busy_mid_run_stops(engine: Engine, tmp_path: Path) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path)
    ids = _watch(engine, "posture corrector", "neck pillow", "ice roller")
    market.answers["neck pillow"] = [("profile_busy", 0)]

    summary = await runner.run_all(DAY1)

    assert market.searched == ["posture corrector", "neck pillow"]  # ice roller not tried
    assert summary.profile_busy and not summary.blocked
    assert [r.status for r in summary.items] == ["ok", "error"]
    assert rec.notes == []
    with Session(engine) as s:
        untouched = get_watch_item(s, ids[2])
        assert untouched is not None and untouched.last_run_at is None


async def test_run_one_reports_profile_busy(engine: Engine, tmp_path: Path) -> None:
    runner, market, _, _ = _setup(engine, tmp_path)
    [item_id] = _watch(engine, "neck pillow")
    market.answers["neck pillow"] = [("profile_busy", 0)]
    result = await runner.run_one(item_id)
    assert result.status == "error" and result.profile_busy
    assert not (await runner.run_one(item_id)).profile_busy


async def test_item_disabled_while_earlier_item_runs_is_skipped(
    engine: Engine, tmp_path: Path
) -> None:
    runner, market, rec, _ = _setup(engine, tmp_path)
    ids = _watch(engine, "posture corrector", "neck pillow", "ice roller")

    def disable_second(q: SearchQuery) -> None:
        if q.keyword == "posture corrector":
            with Session(engine) as s:
                update_watch_item(s, ids[1], enabled=False)

    market.on_search = disable_second
    summary = await runner.run_all(DAY1)

    assert market.searched == ["posture corrector", "ice roller"]
    assert [r.watch_item_id for r in summary.items] == [ids[0], ids[2]]
    assert len(rec.sleeps) == 1  # one polite gap between the two searches that ran
    with Session(engine) as s:
        assert snapshots_for_watch(s, ids[1]) == []


async def test_item_deleted_while_earlier_item_runs_is_not_scraped(
    engine: Engine, tmp_path: Path
) -> None:
    runner, market, _, _ = _setup(engine, tmp_path)
    ids = _watch(engine, "posture corrector", "neck pillow")

    def delete_second(q: SearchQuery) -> None:
        if q.keyword == "posture corrector":
            with Session(engine) as s:
                assert delete_watch_item(s, ids[1])

    market.on_search = delete_second
    summary = await runner.run_all(DAY1)

    assert market.searched == ["posture corrector"]
    assert [r.watch_item_id for r in summary.items] == [ids[0]]
    with Session(engine) as s:
        # No snapshot was created for (or linked to) the deleted item.
        assert [snap.watch_item_id for snap in list_snapshots(s)] == [ids[0]]


async def test_hanging_notifier_times_out(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(runner_module, "NOTIFY_TIMEOUT_S", 0.05)
    runner, market, _, _ = _setup(engine, tmp_path)
    _watch(engine, "posture corrector", "neck pillow")
    market.answers["posture corrector"] = [("blocked", 0)]
    calls: list[str] = []

    async def hang(title: str, body: str) -> None:
        calls.append(body)
        await asyncio.Event().wait()  # a desktop service that never answers

    runner._notifier = hang
    with caplog.at_level("WARNING", logger="adscout.watch.runner"):
        summary = await asyncio.wait_for(runner.run_all(DAY1), 5)

    assert calls == [BLOCKED_MESSAGE]
    assert summary.blocked and summary.items[0].alert == BLOCKED_MESSAGE
    assert "timed out" in caplog.text
    with Session(engine) as s:
        assert len(list_alerts(s)) == 1  # the alert itself is stored
