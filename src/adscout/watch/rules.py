"""Trend series and the alert rule for a watch item's snapshots (pure: no I/O, no clock)."""

import math
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel

from adscout.models import Ad
from adscout.scoring.creatives import creative_key
from adscout.services.report import AdView, Report

# Snapshots that describe the market: a clean run, or one that stopped at the ad limit.
TREND_STATUSES = frozenset({"ok", "capped"})

ALERT_MIN_PREVIOUS = 5  # no alert while the previous count is below this
ALERT_MIN_CHANGE_PCT = 30

BLOCKED_MESSAGE = "Meta blocked the daily check. Open the app to sign in."


class SnapshotSummary(BaseModel):
    """What the watchlist needs from one snapshot's report."""

    id: int
    started_at: AwareDatetime
    status: str
    active_relevant: int  # relevant ads that are still active
    # Raw creative keys (``creative_key(ad)``) of every relevant ad, and the same keys
    # grouped per relevant creative. Raw keys stay stable across snapshots even when the
    # report merges (by image hash) different creatives in one snapshot but not another.
    creative_keys: set[str]
    creatives: list[frozenset[str]] = []
    advertisers: int
    winner: int  # headline Winner Score
    market: int  # pooled market score
    capped: bool  # stopped at the ad limit: the counts are floors


class TrendPoint(BaseModel):
    snapshot_id: int
    date: date  # local date of the run
    started_at: AwareDatetime
    active_ads: int
    capped: bool
    new_creatives: int  # creative keys not seen in any earlier snapshot of the item
    advertisers: int
    winner_score: int
    market_score: int


class AlertDraft(BaseModel):
    kind: Literal["change", "blocked"]
    change_pct: float | None
    message: str


_AD_FIELDS = set(Ad.model_fields)


def _raw_key(view: AdView) -> str:
    return creative_key(Ad.model_validate(view.model_dump(include=_AD_FIELDS)))


def summarize(report: Report) -> SnapshotSummary:
    relevant = [a for a in report.ads if a.relevant]
    groups: dict[str, set[str]] = {}
    for view in relevant:
        groups.setdefault(view.creative_key, set()).add(_raw_key(view))
    return SnapshotSummary(
        id=report.snapshot.id,
        started_at=report.snapshot.started_at,
        status=report.snapshot.status,
        active_relevant=sum(1 for a in relevant if a.is_active),
        creative_keys=set().union(*groups.values()),
        creatives=[frozenset(keys) for keys in groups.values()],
        advertisers=report.score.advertisers,
        winner=report.score.headline,
        market=report.score.total,
        capped=report.snapshot.status == "capped",
    )


def _half_up(x: float) -> int:
    """Round half up (62.5 -> 63); ``round`` would round half to even."""
    return math.floor(x + 0.5)


def _order(s: SnapshotSummary) -> tuple[datetime, int]:
    return s.started_at, s.id


def trend(points_input: list[SnapshotSummary], tz: ZoneInfo) -> list[TrendPoint]:
    """One point per ok/capped snapshot, oldest first; dates are local to ``tz``.

    A creative is new when none of its raw member keys appeared in an earlier snapshot; the
    first snapshot's creatives all count as new. A summary without ``creatives`` counts
    each raw key as its own creative.
    """
    seen: set[str] = set()
    out: list[TrendPoint] = []
    for s in sorted((p for p in points_input if p.status in TREND_STATUSES), key=_order):
        creatives = s.creatives or [frozenset({k}) for k in s.creative_keys]
        new = [c for c in creatives if c.isdisjoint(seen)]
        seen |= s.creative_keys.union(*creatives)
        out.append(
            TrendPoint(
                snapshot_id=s.id,
                date=s.started_at.astimezone(tz).date(),
                started_at=s.started_at,
                active_ads=s.active_relevant,
                capped=s.capped,
                new_creatives=len(new),
                advertisers=s.advertisers,
                winner_score=s.winner,
                market_score=s.market,
            )
        )
    return out


def blocked_alert() -> AlertDraft:
    return AlertDraft(kind="blocked", change_pct=None, message=BLOCKED_MESSAGE)


def alert_for(prev: SnapshotSummary | None, now: SnapshotSummary, query: str) -> AlertDraft | None:
    """The alert for ``now`` against the item's previous ok/capped snapshot, if any.

    A blocked run raises the block alert. Otherwise alert when the relevant active-ad count
    moved by at least 30% from a previous count of 5 or more. Capped counts are floors: both
    capped never alerts; only ``now`` capped alerts on an increase only (worded "≥ +N%");
    only ``prev`` capped alerts on a decrease only.
    """
    if now.status == "blocked":
        return blocked_alert()
    if prev is None or prev.status not in TREND_STATUSES or now.status not in TREND_STATUSES:
        return None
    before, after = prev.active_relevant, now.active_relevant
    if before < ALERT_MIN_PREVIOUS:
        return None
    delta = after - before
    # Integer comparison: |delta| / before >= 30%.
    if delta == 0 or abs(delta) * 100 < ALERT_MIN_CHANGE_PCT * before:
        return None
    up = delta > 0
    if prev.capped and now.capped:
        return None
    if now.capped and not up:
        return None
    if prev.capped and up:
        return None
    pct = 100 * delta / before
    # A capped count is a floor: round down so the percentage is never overstated.
    shown = f"≥ +{int(abs(pct))}%" if now.capped else f"{_half_up(abs(pct))}%"
    direction = "up" if up else "down"
    return AlertDraft(
        kind="change",
        change_pct=round(pct, 1),
        message=f"{query}: active ads {direction} {shown} ({before} → {after})",
    )
