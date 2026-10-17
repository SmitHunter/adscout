"""Load a snapshot and score it: the one `Report` model the CLI and the GUI both render."""

import re
from collections import Counter
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ValidationError
from sqlmodel import Session

from adscout.analysis.models import AngleAnalysis, Emotion
from adscout.db.repo import latest_analysis, load_ads, snapshot_kind, snapshot_overrides
from adscout.db.tables import Analysis, Snapshot
from adscout.models import Ad, SearchKind
from adscout.models.ad import MediaItem
from adscout.scoring import TimelineBucket, WinnerScore, group_creatives, score_ads, timeline
from adscout.scoring.creatives import Creative
from adscout.scoring.relevance import RelevanceReason
from adscout.services.analysis import angle_input_from

_PARTIAL_STATUSES = ("blocked", "cancelled")
_LIMIT_IN_MESSAGE = re.compile(r"\b(\d+) ads?\b")


class SnapshotView(BaseModel):
    id: int
    query: str  # the keyword, or the advertiser's name
    kind: SearchKind
    page_id: str | None
    country: str
    active_status: str
    media_type: str
    status: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    ad_count: int
    advertiser_count: int
    error_message: str | None


class AdView(BaseModel):
    """Every `Ad` field except `raw`, plus the relevance decision and derived display fields."""

    source: str
    ad_archive_id: str
    page_id: str
    page_name: str
    start_date: AwareDatetime
    # None while the ad is active: Meta can report a stale end date on a running ad.
    end_date: AwareDatetime | None
    is_active: bool
    body: str
    title: str | None
    link_url: str | None
    link_caption: str | None
    cta_text: str | None
    display_format: str | None
    media: list[MediaItem]
    collation_id: str | None
    collation_count: int
    platforms: list[str]
    relevant: bool
    reason: RelevanceReason
    creative_key: str
    advertiser_key: str | None  # Ad.advertiser_key: the one key rule, shared with the GUI
    days_running: int
    landing_domain: str | None


class AdvertiserRow(BaseModel):
    key: str
    name: str
    ads: int  # relevant ads
    total_ads: int  # all ads
    creatives: int  # relevant creatives
    # oldest_start, days_running_max, landing_domain: over relevant ads, else all the ads.
    oldest_start: AwareDatetime
    days_running_max: int
    landing_domain: str | None
    excluded: bool
    is_leader: bool


class TimelineBucketView(TimelineBucket):
    leader_new_creatives: int


class AnalysisAngleView(BaseModel):
    name: str
    description: str
    emotion: Emotion
    ad_ids: list[str]  # as returned: creative-lead ids
    creatives: int  # creatives in this angle with at least one relevant ad now
    ads: int  # relevant ads across those creatives


class AnalysisView(BaseModel):
    """The latest stored analysis. ``result`` is as saved; ``angles`` carries live counts."""

    result: AngleAnalysis
    angles: list[AnalysisAngleView]
    model: str
    backend: str
    created_at: AwareDatetime
    ad_count: int  # creatives sent to Claude
    # Ads currently excluded because Claude flagged their creative (reason claude_off_topic).
    flagged_off_topic: int


class Report(BaseModel):
    snapshot: SnapshotView
    score: WinnerScore
    timeline_month: list[TimelineBucketView]
    timeline_week: list[TimelineBucketView]
    advertisers: list[AdvertiserRow]
    ads: list[AdView]
    notice: str | None
    analysis: AnalysisView | None


def snapshot_view(snap: Snapshot) -> SnapshotView:
    assert snap.id is not None
    return SnapshotView(
        id=snap.id,
        query=snap.query,
        kind=snapshot_kind(snap),
        page_id=snap.page_id,
        country=snap.country,
        active_status=snap.active_status,
        media_type=snap.media_type,
        status=snap.status,
        started_at=snap.started_at,
        finished_at=snap.finished_at,
        ad_count=snap.ad_count,
        advertiser_count=snap.advertiser_count,
        error_message=snap.error_message,
    )


def landing_domain(url: str | None) -> str | None:
    """Host of `url` without a leading `www.`; None when there is no usable host."""
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.removeprefix("www.")


QUEUED_NOTICE = "This search is waiting for another search to finish."


def _notice(snap: Snapshot) -> str | None:
    if snap.status == "queued":
        return QUEUED_NOTICE
    if snap.ad_count == 0 and snap.status != "ok":
        return f"This snapshot has no saved ads ({snap.status})."
    if snap.status == "error":
        return f"This search stopped with an error: {snap.error_message or 'unknown error'}"
    if snap.status == "capped":
        # The source's message names the limit; without one, the saved ads hit it.
        found = _LIMIT_IN_MESSAGE.search(snap.error_message or "")
        limit = found.group(1) if found else str(snap.ad_count)
        return f"Stopped at the {limit} ad limit. Raise the limit to see more."
    if snap.status in _PARTIAL_STATUSES:
        return f"Partial data: this snapshot stopped early ({snap.status})."
    return None


def _days(ad: Ad, now: datetime) -> int:
    return max(0, ad.days_running(now))


def _advertiser_rows(
    ads: list[Ad],
    relevant_ids: set[str],
    excluded_keys: set[str],
    leader_key: str | None,
    now: datetime,
) -> list[AdvertiserRow]:
    by_key: dict[str, list[Ad]] = {}
    for ad in ads:
        if (key := ad.advertiser_key) is not None:
            by_key.setdefault(key, []).append(ad)
    rows: list[AdvertiserRow] = []
    for key, group in by_key.items():
        relevant = [a for a in group if a.ad_archive_id in relevant_ids]
        excluded = key in excluded_keys
        shown = relevant or group
        domains = Counter(d for a in shown if (d := landing_domain(a.link_url)) is not None)
        oldest = min(a.start_date for a in shown)
        first = shown[0]
        rows.append(
            AdvertiserRow(
                key=key,
                name=first.page_name.strip() or first.page_id or key,
                ads=len(relevant),
                total_ads=len(group),
                creatives=len(group_creatives(relevant)),
                oldest_start=oldest,
                days_running_max=max(_days(a, now) for a in shown),
                landing_domain=domains.most_common(1)[0][0] if domains else None,
                excluded=excluded,
                is_leader=key == leader_key,
            )
        )
    rows.sort(key=lambda r: (-r.creatives, -r.ads, -r.total_ads, r.key))
    return rows


def _timeline_views(
    creatives: list[Creative],
    leader_key: str | None,
    tz: ZoneInfo,
    bucket: Literal["month", "week"],
) -> list[TimelineBucketView]:
    mine = [
        c
        for c in creatives
        if leader_key is not None and any(a.advertiser_key == leader_key for a in c.ads)
    ]
    leader_counts = {b.start: b.new_creatives for b in timeline(mine, tz, bucket)}
    return [
        TimelineBucketView(**b.model_dump(), leader_new_creatives=leader_counts.get(b.start, 0))
        for b in timeline(creatives, tz, bucket)
    ]


def _stored_analysis(row: Analysis | None) -> AngleAnalysis | None:
    """The stored result, or None if absent or no longer readable (e.g. an older schema)."""
    if row is None:
        return None
    try:
        return AngleAnalysis.model_validate(row.result)
    except ValidationError:
        return None


def _analysis_view(
    row: Analysis,
    result: AngleAnalysis,
    creatives: list[Creative],
    relevant_ids: set[str],
    flagged_off_topic: int,
) -> AnalysisView:
    """Live, creative-level view: each lead id stands for its whole creative group."""
    group_of: dict[str, Creative] = {m.ad_archive_id: c for c in creatives for m in c.ads}

    def live(ids: list[str]) -> list[list[str]]:
        """Relevant ad ids per distinct creative behind ``ids``; creatives with none dropped."""
        seen: set[str] = set()
        out: list[list[str]] = []
        for i in ids:
            group = group_of.get(i)
            if group is None or group.key in seen:
                continue
            seen.add(group.key)
            members = [m.ad_archive_id for m in group.ads if m.ad_archive_id in relevant_ids]
            if members:
                out.append(members)
        return out

    angle_views: list[AnalysisAngleView] = []
    for a in result.angles:
        groups = live(a.ad_ids)
        angle_views.append(
            AnalysisAngleView(
                **a.model_dump(), creatives=len(groups), ads=sum(len(g) for g in groups)
            )
        )
    return AnalysisView(
        result=result,
        angles=angle_views,
        model=row.model,
        backend=row.backend,
        created_at=row.created_at,
        ad_count=row.ad_count,
        flagged_off_topic=flagged_off_topic,
    )


def build_report(
    session: Session,
    snapshot_id: int,
    now: datetime,
    tz: ZoneInfo,
    analysis: Analysis | None = None,
) -> Report:
    """Score a snapshot with the keyword's stored overrides and assemble the full report.

    Angles come from ``analysis`` when given, else the snapshot's latest stored analysis.
    """
    snap = session.get(Snapshot, snapshot_id)
    if snap is None or snap.id is None:
        raise LookupError(f"snapshot {snapshot_id} not found")
    ads = load_ads(session, snapshot_id)
    overrides = snapshot_overrides(session, snap)
    row = analysis if analysis is not None else latest_analysis(session, snapshot_id)
    stored = _stored_analysis(row)
    angle_input = None if stored is None else angle_input_from(stored)
    score = score_ads(
        ads, snap.query, overrides, now, tz, angle_input=angle_input, kind=snapshot_kind(snap)
    )
    decisions = score.decisions
    relevant = [ad for ad in ads if decisions[ad.ad_archive_id].relevant]
    relevant_ids = {ad.ad_archive_id for ad in relevant}
    leader_key = score.leader.advertiser_key if score.leader else None

    # Group the relevant ads once (timeline) and reuse it for the per-ad keys when nothing
    # was excluded; only a snapshot with excluded ads needs a second, pooled grouping.
    relevant_creatives = group_creatives(relevant)
    all_creatives = relevant_creatives if len(relevant) == len(ads) else group_creatives(ads)
    key_of: dict[str, str] = {}
    for creative in all_creatives:
        for member in creative.ads:
            key_of[member.ad_archive_id] = creative.key

    ad_views = [
        AdView(
            **ad.model_dump(exclude={"raw", "end_date"}),
            end_date=None if ad.is_active else ad.end_date,
            relevant=decisions[ad.ad_archive_id].relevant,
            reason=decisions[ad.ad_archive_id].reason,
            creative_key=key_of[ad.ad_archive_id],
            advertiser_key=ad.advertiser_key,
            days_running=_days(ad, now),
            landing_domain=landing_domain(ad.link_url),
        )
        for ad in ads
    ]
    excluded_keys = {
        o.target_id for o in overrides if o.scope == "advertiser" and o.mode == "exclude"
    }
    return Report(
        snapshot=snapshot_view(snap),
        score=score,
        timeline_month=_timeline_views(relevant_creatives, leader_key, tz, "month"),
        timeline_week=_timeline_views(relevant_creatives, leader_key, tz, "week"),
        advertisers=_advertiser_rows(ads, relevant_ids, excluded_keys, leader_key, now),
        ads=ad_views,
        notice=_notice(snap),
        analysis=(
            None
            if row is None or stored is None
            else _analysis_view(
                row,
                stored,
                all_creatives,
                relevant_ids,
                sum(1 for d in decisions.values() if d.reason == "claude_off_topic"),
            )
        ),
    )
