"""Watchlist views for the API: each item with its latest result, sparkline and alerts."""

import hashlib
import json
import threading
from collections import Counter, OrderedDict
from collections.abc import Collection, Hashable
from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel
from sqlmodel import Session, col, select

from adscout.db.repo import (
    latest_analysis,
    list_alerts,
    snapshot_overrides,
    snapshots_for_watch,
)
from adscout.db.tables import AdRow, Alert, Snapshot, WatchItem
from adscout.models import SearchKind
from adscout.services.report import SnapshotView, build_report, snapshot_view
from adscout.watch.rules import TREND_STATUSES, SnapshotSummary, TrendPoint, summarize, trend

SPARKLINE_POINTS = 14
# A watch item whose search is waiting or running is "being checked".
IN_PROGRESS_STATUSES = ("queued", "running")


class LatestPoint(BaseModel):
    """The item's latest ok/capped result (a trend point without ``new_creatives``, which
    needs the whole history)."""

    snapshot_id: int
    date: date
    started_at: AwareDatetime
    active_ads: int
    capped: bool
    advertisers: int
    winner_score: int
    market_score: int


class WatchItemView(BaseModel):
    id: int
    kind: SearchKind
    query: str  # the keyword, or the advertiser's name
    page_id: str | None
    country: str
    active_status: str
    media_type: str
    enabled: bool
    created_at: AwareDatetime
    last_run_at: AwareDatetime | None
    last_status: str | None
    latest: SnapshotView | None  # the most recent snapshot, whatever its status
    latest_point: LatestPoint | None
    sparkline: list[int]  # relevant active ads of the last 14 ok/capped snapshots
    next_run_at: AwareDatetime | None  # the next daily check, for enabled items
    unread_alerts: int
    running: bool  # a check of this item is queued or running


class WatchTrend(BaseModel):
    item: WatchItemView
    points: list[TrendPoint]


class AlertView(BaseModel):
    id: int
    watch_item_id: int
    snapshot_id: int | None
    query: str  # the watch item's keyword or advertiser name
    kind: Literal["change", "blocked"]
    change_pct: float | None
    message: str
    created_at: AwareDatetime
    read: bool


def _kind(value: str) -> SearchKind:
    return "advertiser" if value == "advertiser" else "keyword"


# Scored summaries of finished snapshots, reused across requests: the watchlist polls
# every minute and would otherwise re-score every snapshot of every item each time.
_SUMMARY_CACHE_SIZE = 2048
_summary_cache: OrderedDict[Hashable, SnapshotSummary] = OrderedDict()
_summary_lock = threading.Lock()


def clear_summary_cache() -> None:
    with _summary_lock:
        _summary_cache.clear()


def _summary_key(session: Session, snap: Snapshot, now: datetime, tz: ZoneInfo) -> Hashable:
    """Everything a finished snapshot's summary depends on.

    The snapshot itself (its database, row and finish), its ads' media (the media phase
    and ``adscout media`` write phashes after the search, and creatives merge by phash),
    the user's overrides for its keyword, its latest analysis, the time zone, and ``now``
    to the hour: day counts and the continuity window move with the clock, so a summary
    is at most an hour old.
    """
    analysis = latest_analysis(session, snap.id) if snap.id is not None else None
    overrides = tuple(
        sorted((o.scope, o.target_id, o.mode) for o in snapshot_overrides(session, snap))
    )
    bind = session.get_bind()
    return (
        str(bind.engine.url),
        snap.id,
        snap.status,
        snap.finished_at,
        snap.ad_count,
        _media_fingerprint(session, snap.id),
        None if analysis is None else (analysis.id, analysis.created_at),
        overrides,
        tz.key,
        now.astimezone(UTC).replace(minute=0, second=0, microsecond=0),
    )


def _media_fingerprint(session: Session, snapshot_id: int | None) -> str:
    """A digest of every ad's stored media JSON: changes whenever media is written."""
    stmt = (
        select(AdRow.ad_archive_id, AdRow.media)
        .where(AdRow.snapshot_id == snapshot_id)
        .order_by(col(AdRow.ad_archive_id))
    )
    digest = hashlib.sha256()
    for ad_id, media in session.exec(stmt):
        digest.update(json.dumps([ad_id, media], sort_keys=True, default=str).encode())
    return digest.hexdigest()


def _summary(session: Session, snap: Snapshot, now: datetime, tz: ZoneInfo) -> SnapshotSummary:
    assert snap.id is not None
    key = _summary_key(session, snap, now, tz)
    with _summary_lock:
        cached = _summary_cache.get(key)
        if cached is not None:
            _summary_cache.move_to_end(key)
            return cached
    summary = summarize(build_report(session, snap.id, now, tz))
    with _summary_lock:
        _summary_cache[key] = summary
        while len(_summary_cache) > _SUMMARY_CACHE_SIZE:
            _summary_cache.popitem(last=False)
    return summary


def _summaries(
    session: Session, snaps: list[Snapshot], now: datetime, tz: ZoneInfo
) -> list[SnapshotSummary]:
    return [
        _summary(session, s, now, tz)
        for s in snaps
        if s.id is not None and s.status in TREND_STATUSES
    ]


def _latest_point(s: SnapshotSummary, tz: ZoneInfo) -> LatestPoint:
    return LatestPoint(
        snapshot_id=s.id,
        date=s.started_at.astimezone(tz).date(),
        started_at=s.started_at,
        active_ads=s.active_relevant,
        capped=s.capped,
        advertisers=s.advertisers,
        winner_score=s.winner,
        market_score=s.market,
    )


def busy_watch_items(session: Session, ignore_snapshots: Collection[int] = ()) -> set[int]:
    """Watch items with a snapshot "queued" or "running" in the DB (any process), leaving
    out the snapshots in ``ignore_snapshots``."""
    stmt = select(Snapshot.watch_item_id, Snapshot.id).where(
        col(Snapshot.watch_item_id).is_not(None),
        col(Snapshot.status).in_(IN_PROGRESS_STATUSES),
    )
    ignored = set(ignore_snapshots)
    return {i for i, sid in session.exec(stmt) if i is not None and sid not in ignored}


def _unread_counts(session: Session) -> Counter[int]:
    return Counter(a.watch_item_id for a in list_alerts(session, unread_only=True))


def watch_item_view(
    session: Session,
    item: WatchItem,
    now: datetime,
    tz: ZoneInfo,
    *,
    next_run: datetime | None,
    running: bool,
    unread: int,
) -> WatchItemView:
    """One item's view. Scores only the last 14 ok/capped snapshots (the sparkline)."""
    assert item.id is not None
    snaps = snapshots_for_watch(session, item.id)
    recent = [s for s in snaps if s.status in TREND_STATUSES][-SPARKLINE_POINTS:]
    summaries = _summaries(session, recent, now, tz)
    return WatchItemView(
        id=item.id,
        kind=_kind(item.kind),
        query=item.query,
        page_id=item.page_id,
        country=item.country,
        active_status=item.active_status,
        media_type=item.media_type,
        enabled=item.enabled,
        created_at=item.created_at,
        last_run_at=item.last_run_at,
        last_status=item.last_status,
        latest=snapshot_view(snaps[-1]) if snaps else None,
        latest_point=_latest_point(summaries[-1], tz) if summaries else None,
        sparkline=[s.active_relevant for s in summaries],
        next_run_at=next_run if item.enabled else None,
        unread_alerts=unread,
        running=running,
    )


def watch_item_views(
    session: Session,
    items: list[WatchItem],
    now: datetime,
    tz: ZoneInfo,
    *,
    next_run: datetime | None,
    running_ids: set[int],
) -> list[WatchItemView]:
    """``running_ids``: the items being checked right now (see the watch routes)."""
    unread = _unread_counts(session)
    return [
        watch_item_view(
            session,
            item,
            now,
            tz,
            next_run=next_run,
            running=item.id in running_ids,
            unread=unread[item.id] if item.id is not None else 0,
        )
        for item in items
    ]


def item_trend(session: Session, item_id: int, now: datetime, tz: ZoneInfo) -> list[TrendPoint]:
    """The item's full trend series (scores every ok/capped snapshot)."""
    return trend(_summaries(session, snapshots_for_watch(session, item_id), now, tz), tz)


def alert_views(
    session: Session, *, unread_only: bool = False, limit: int | None = None
) -> list[AlertView]:
    """Alerts, newest first (at most ``limit``), with their watch item's query."""
    alerts = list_alerts(session, unread_only=unread_only, limit=limit)
    ids = {a.watch_item_id for a in alerts}
    queries = {
        item.id: item.query
        for item in session.exec(select(WatchItem).where(col(WatchItem.id).in_(ids)))
    }
    return [_alert_view(a, queries.get(a.watch_item_id, "")) for a in alerts]


def _alert_view(alert: Alert, query: str) -> AlertView:
    assert alert.id is not None
    return AlertView(
        id=alert.id,
        watch_item_id=alert.watch_item_id,
        snapshot_id=alert.snapshot_id,
        query=query,
        kind="blocked" if alert.kind == "blocked" else "change",
        change_pct=alert.change_pct,
        message=alert.message,
        created_at=alert.created_at,
        read=alert.read,
    )
