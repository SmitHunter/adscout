"""WatchRunner: re-run the enabled watch items, politely, and raise alerts.

A daily run checks one item at a time (each search queued as "scheduled" and awaited to
its end), waits a random 30-90 s between items, stops at the first blocked search, and is
recorded in ``<data_dir>/watch_runs.json`` so the same local day never runs twice. A run
that finds the browser profile busy (the AdScout window open, or another process using it)
records nothing, so the scheduler can retry later that day.
"""

import asyncio
import json
import logging
import os
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.config import Settings
from adscout.db.repo import (
    add_alert,
    get_watch_item,
    list_watch_items,
    snapshots_for_watch,
    update_watch_item,
)
from adscout.db.tables import Snapshot, WatchItem
from adscout.locks import PROFILE_BUSY_MESSAGE, ProfileBusy, check_profile_free, profile_lock_dir
from adscout.models import SearchQuery
from adscout.services.report import build_report
from adscout.services.searches import SearchBusy, SearchManager
from adscout.watch import notify
from adscout.watch.rules import TREND_STATUSES, AlertDraft, alert_for, blocked_alert, summarize

logger = logging.getLogger(__name__)

WATCH_RUNS_FILE = "watch_runs.json"
GAP_MIN_S = 30.0
GAP_MAX_S = 90.0
NOTIFY_TITLE = notify.APP_NAME
NOTIFY_TIMEOUT_S = 10.0

Sleep = Callable[[float], Awaitable[None]]
Notifier = Callable[[str, str], Awaitable[None]]


class WatchRunRecord(BaseModel):
    """The latest daily run: its local date and when it started and finished."""

    date: date
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class ItemRun(BaseModel):
    watch_item_id: int
    query: str
    snapshot_id: int | None
    status: str  # the snapshot's final status ("ok", "capped", "blocked", ...)
    alert: str | None = None
    # The search failed because another AdScout process holds the browser profile.
    profile_busy: bool = False


class RunSummary(BaseModel):
    started_at: AwareDatetime
    finished_at: AwareDatetime
    items: list[ItemRun] = []
    blocked: bool = False
    # Stopped part-way: another AdScout process took the browser profile.
    profile_busy: bool = False
    # Why nothing ran: already ran today, nothing enabled, a run is in progress, or the
    # browser profile is in use (then nothing is recorded and the scheduler retries).
    skipped: Literal["already_ran", "no_items", "busy", "profile_busy"] | None = None


def read_last_run(data_dir: Path) -> WatchRunRecord | None:
    """The recorded daily run, or None when missing or unreadable."""
    try:
        raw = (data_dir / WATCH_RUNS_FILE).read_text(encoding="utf-8")
        return WatchRunRecord.model_validate_json(raw)
    except (OSError, ValueError):
        return None


def write_last_run(data_dir: Path, record: WatchRunRecord) -> None:
    """Write the record atomically (temp file + replace)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / WATCH_RUNS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record.model_dump(mode="json"), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _query(item: WatchItem, limit: int) -> SearchQuery:
    return SearchQuery.model_validate(
        {
            "kind": item.kind,
            "keyword": item.query,
            "page_id": item.page_id,
            "country": item.country,
            "active_status": item.active_status,
            "media_type": item.media_type,
            "limit": limit,
        }
    )


class WatchRunner:
    """Runs watch items through the ``SearchManager`` and turns their results into alerts."""

    def __init__(
        self,
        engine: Engine,
        manager: SearchManager,
        *,
        settings: Callable[[], Settings],
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        notifier: Notifier = notify.notify,
        clock: Callable[[], datetime] = _utcnow,
        browser_open: Callable[[], bool] = lambda: False,
    ) -> None:
        self._engine = engine
        self._manager = manager
        self._settings = settings
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()
        self._notifier = notifier
        self._clock = clock
        self._browser_open = browser_open
        self._lock = asyncio.Lock()

    async def has_enabled_items(self) -> bool:
        return bool(await asyncio.to_thread(self._enabled_items))

    async def run_all(self, now: datetime) -> RunSummary:
        """The daily run: every enabled item, one at a time, at most once per local day."""
        settings = self._settings()
        today = now.astimezone(ZoneInfo(settings.timezone)).date()
        if self._lock.locked():
            logger.info("Daily watch check skipped: a run is already in progress")
            return RunSummary(started_at=now, finished_at=now, skipped="busy")
        async with self._lock:
            last = read_last_run(settings.data_dir)
            if last is not None and last.date == today:
                logger.info("Daily watch check skipped: it already ran today (%s)", today)
                return RunSummary(started_at=now, finished_at=now, skipped="already_ran")
            items = await asyncio.to_thread(self._enabled_items)
            if not items:
                logger.info("Daily watch check: no enabled watch items, nothing to do")
                return RunSummary(started_at=now, finished_at=now, skipped="no_items")
            if await self._profile_busy(settings):
                # Nothing recorded: the day's check is still owed (the scheduler retries).
                logger.info("Daily watch check skipped: the browser profile is in use")
                return RunSummary(started_at=now, finished_at=now, skipped="profile_busy")
            # Keyed by the local date the run *executes* on, not the slot it was meant for, on
            # purpose: politeness means Meta never sees two daily runs on one day. So a startup
            # catch-up before today's slot (e.g. 08:00 for yesterday's missed 09:00) is today's
            # check, and the 09:00 job then skips. Recorded before the first search, so a crash
            # mid-run still counts as the day's run.
            record = WatchRunRecord(date=today, started_at=now)
            write_last_run(settings.data_dir, record)
            logger.info("Daily watch check: %d item(s)", len(items))
            summary = RunSummary(started_at=now, finished_at=now)
            try:
                gap_due = False
                for listed in items:
                    assert listed.id is not None
                    if gap_due:
                        await self._sleep(self._rng.uniform(GAP_MIN_S, GAP_MAX_S))
                        gap_due = False
                    # Re-read just before its search: the item may have been disabled or
                    # removed while earlier items ran.
                    item = await asyncio.to_thread(self._current_item, listed.id)
                    if item is None or not item.enabled:
                        logger.info("Watch item %s skipped: disabled or removed", listed.id)
                        continue
                    try:
                        result, profile_busy = await self._run_item(item)
                    except SearchBusy:
                        logger.info("Daily watch check stopped: AdScout is shutting down")
                        break
                    gap_due = True
                    summary.items.append(result)
                    if profile_busy:
                        summary.profile_busy = True
                        logger.warning(
                            "Daily watch check stopped: another AdScout process is using "
                            "the browser"
                        )
                        break
                    if result.status == "blocked":
                        summary.blocked = True
                        logger.warning("Daily watch check stopped: Meta blocked the search")
                        break
            finally:
                finished = self._clock()
                summary.finished_at = finished
                write_last_run(
                    settings.data_dir, record.model_copy(update={"finished_at": finished})
                )
            return summary

    async def run_one(self, item_id: int) -> ItemRun:
        """Check one item now ("Run now"): no gap and no daily record.

        Raises ``LookupError`` for an unknown item.
        """
        item = await asyncio.to_thread(self._get_item, item_id)
        run, _profile_busy = await self._run_item(item)
        return run

    async def _profile_busy(self, settings: Settings) -> bool:
        """Whether the browser profile is unavailable to a daily run right now.

        The AdScout browser window, or another process holding the profile lock, is busy.
        A search of this process's own manager is not: scheduled searches queue behind it.
        """
        if self._browser_open():
            return True
        if self._manager.active() is not None:
            return False
        try:
            await asyncio.to_thread(
                check_profile_free, profile_lock_dir(settings.browser_profile_dir)
            )
        except ProfileBusy:
            return True
        return False

    def _current_item(self, item_id: int) -> WatchItem | None:
        with Session(self._engine) as session:
            return get_watch_item(session, item_id)

    def _enabled_items(self) -> list[WatchItem]:
        with Session(self._engine) as session:
            return list_watch_items(session, enabled_only=True)

    def _get_item(self, item_id: int) -> WatchItem:
        with Session(self._engine) as session:
            item = get_watch_item(session, item_id)
        if item is None:
            raise LookupError(f"watch item {item_id} not found")
        return item

    async def _run_item(self, item: WatchItem) -> tuple[ItemRun, bool]:
        """Run one item's search to its end; also say whether it failed because another
        process holds the browser profile (``ProfileBusy``)."""
        assert item.id is not None
        settings = self._settings()
        q = _query(item, settings.max_results)
        snapshot_id = await self._manager.enqueue(q, "scheduled", watch_item_id=item.id)
        profile_busy = False
        async for event in self._manager.subscribe(snapshot_id):
            # The generator ends after the search's terminal event.
            profile_busy = event.kind == "error" and event.message == PROFILE_BUSY_MESSAGE
        status, draft = await asyncio.to_thread(
            self._conclude, item, snapshot_id, ZoneInfo(settings.timezone)
        )
        if draft is not None and settings.desktop_notifications:
            await self._notify(draft.message)
        run = ItemRun(
            watch_item_id=item.id,
            query=item.query,
            snapshot_id=snapshot_id,
            status=status,
            alert=draft.message if draft is not None else None,
            profile_busy=profile_busy,
        )
        return run, profile_busy

    async def _notify(self, message: str) -> None:
        """Best effort: a failing or hanging notifier never holds up the run."""
        try:
            await asyncio.wait_for(self._notifier(NOTIFY_TITLE, message), NOTIFY_TIMEOUT_S)
        except TimeoutError:
            logger.warning("The desktop notification timed out after %.0f s", NOTIFY_TIMEOUT_S)
        except Exception:
            logger.warning("Could not send the desktop notification", exc_info=True)

    def _conclude(
        self, item: WatchItem, snapshot_id: int, tz: ZoneInfo
    ) -> tuple[str, AlertDraft | None]:
        """Record the item's run and store its alert, if any (in a worker thread)."""
        assert item.id is not None
        now = self._clock()
        with Session(self._engine) as session:
            snap = session.get(Snapshot, snapshot_id)
            status = snap.status if snap is not None else "error"
            draft: AlertDraft | None = None
            if status == "blocked":
                draft = blocked_alert()
            elif status in TREND_STATUSES:
                current = summarize(build_report(session, snapshot_id, now, tz))
                earlier = [
                    s
                    for s in snapshots_for_watch(session, item.id)
                    if s.id != snapshot_id and s.status in TREND_STATUSES
                ]
                previous = None
                if earlier and earlier[-1].id is not None:
                    previous = summarize(build_report(session, earlier[-1].id, now, tz))
                draft = alert_for(previous, current, item.query)
            if draft is not None and get_watch_item(session, item.id) is None:
                logger.info("Watch item %s was removed during its check: no alert", item.id)
                draft = None
            if draft is not None:
                add_alert(
                    session,
                    watch_item_id=item.id,
                    snapshot_id=snapshot_id,
                    kind=draft.kind,
                    message=draft.message,
                    change_pct=draft.change_pct,
                )
                logger.info("Watch alert: %s", draft.message)
            try:
                update_watch_item(session, item.id, last_run_at=now, last_status=status)
            except LookupError:
                logger.info("Watch item %s was removed during its check", item.id)
        return status, draft
