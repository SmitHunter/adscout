"""Winner Score factor functions (pure: `now` and `tz` are parameters)."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from adscout.models import Ad
from adscout.scoring.creatives import Creative
from adscout.scoring.thresholds import (
    ANGLE_CLUSTERS,
    CONTINUITY_MONTHS,
    LONGEVITY_DAYS,
    LONGEVITY_SHARE_90D,
    SATURATION_BANDS,
    VOLUME_CREATIVES,
    WEIGHTS,
    FactorKey,
    Threshold,
)

Status = Literal["pass", "warn", "fail"]
Saturation = Literal["early", "healthy", "crowded"]


class FactorResult(BaseModel):
    key: FactorKey
    label: str
    value: float | None
    display: str
    threshold: Threshold
    status: Literal["pass", "warn", "fail", "pending"]
    score: float | None
    points: float | None
    max_points: int
    detail: str


def ramp(v: float, t: Threshold) -> float:
    if v < t.fail:
        return 0.0
    if v < t.pass_:
        high = 0.7 if t.full is not None else 1.0
        return 0.3 + (high - 0.3) * (v - t.fail) / (t.pass_ - t.fail)
    if t.full is not None and v < t.full:
        return 0.7 + 0.3 * (v - t.pass_) / (t.full - t.pass_)
    return 1.0


def status(v: float, t: Threshold) -> Status:
    if v < t.fail:
        return "fail"
    if v >= t.pass_:
        return "pass"
    return "warn"


def _result(
    key: FactorKey,
    label: str,
    value: float,
    display: str,
    threshold: Threshold,
    score: float,
    detail: str,
    status_value: float | None = None,
) -> FactorResult:
    weight = WEIGHTS[key]
    return FactorResult(
        key=key,
        label=label,
        value=value,
        display=display,
        threshold=threshold,
        status=status(value if status_value is None else status_value, threshold),
        score=score,
        points=round(weight * score, 1),
        max_points=weight,
        detail=detail,
    )


def longevity(ads: list[Ad], creatives: list[Creative], now: datetime) -> FactorResult:
    """Oldest ACTIVE ad's age, blended with the share of creatives first seen >= 90 days ago."""
    oldest = max((max(0, ad.days_running(now)) for ad in ads if ad.is_active), default=0)
    aged = sum(1 for c in creatives if max(0, (now - c.first_start).days) >= 90)
    share = aged / len(creatives) if creatives else 0.0
    score = 0.5 * ramp(oldest, LONGEVITY_DAYS) + 0.5 * ramp(share, LONGEVITY_SHARE_90D)
    return _result(
        "longevity",
        "Longevity",
        oldest,
        f"{oldest} days",
        LONGEVITY_DAYS,
        score,
        f"{round(share * 100)}% of creatives running ≥ 90 days",
    )


def continuity(creatives: list[Creative], now: datetime, tz: ZoneInfo) -> FactorResult:
    now_local = now.astimezone(tz)
    now_index = now_local.year * 12 + now_local.month - 1
    window = set(range(now_index - 5, now_index + 1))
    hit: set[int] = set()
    for c in creatives:
        local = c.first_start.astimezone(tz)
        idx = local.year * 12 + local.month - 1
        if idx in window:
            hit.add(idx)
    n = len(hit)
    return _result(
        "continuity",
        "Continuity",
        n,
        f"{n}/6 months",
        CONTINUITY_MONTHS,
        ramp(n, CONTINUITY_MONTHS),
        "Months of the last 6 with at least one new creative",
    )


def volume(creatives: list[Creative]) -> FactorResult:
    n = len(creatives)
    return _result(
        "volume",
        "Creative volume",
        n,
        f"{n} creatives",
        VOLUME_CREATIVES,
        ramp(n, VOLUME_CREATIVES),
        "Distinct creatives (duplicates grouped)",
    )


def angles_pending() -> FactorResult:
    return FactorResult(
        key="angles",
        label="Angle diversity",
        value=None,
        display="—",
        threshold=ANGLE_CLUSTERS,
        status="pending",
        score=None,
        points=None,
        max_points=WEIGHTS["angles"],
        detail="Needs angle analysis",
    )


class AngleInput(BaseModel):
    """The stored analysis's angles as (name, ids), re-filtered at scoring time.

    From the analysis the ids are creative-lead ad ids; ``score_ads`` maps them to creative
    keys before calling :func:`angles` / :func:`leader_angles`, which only compare id sets.
    """

    angles: list[tuple[str, list[str]]]
    # Creative-lead ids Claude marked as not selling the product; excluded at scoring time.
    not_product: list[str] = []


ANGLE_MARKET_MIN_CREATIVES = 2
TOP_ANGLES = 3


def _angles_result(
    counts: list[tuple[str, int]], minimum: int, detail_if_none: str
) -> FactorResult:
    """Angles with at least ``minimum`` matching ids; detail names the top three by count."""
    qualifying = [(name, n) for name, n in counts if n >= minimum]
    n = len(qualifying)
    # sorted() is stable: equal counts keep Claude's order.
    top = [name for name, _ in sorted(qualifying, key=lambda t: -t[1])[:TOP_ANGLES]]
    return _result(
        "angles",
        "Angle diversity",
        n,
        f"{n} angle{'' if n == 1 else 's'}",
        ANGLE_CLUSTERS,
        ramp(n, ANGLE_CLUSTERS),
        ", ".join(top) if top else detail_if_none,
    )


def _counts(ids: set[str], angle_input: AngleInput) -> list[tuple[str, int]]:
    return [(name, len(set(members) & ids)) for name, members in angle_input.angles]


def angles(relevant: set[str], angle_input: AngleInput) -> FactorResult:
    """Market rule: angles backed by at least two distinct currently-relevant creatives."""
    return _angles_result(
        _counts(relevant, angle_input),
        ANGLE_MARKET_MIN_CREATIVES,
        "No angle is backed by 2 or more relevant creatives",
    )


def leader_angles(leader: set[str], angle_input: AngleInput) -> FactorResult:
    """Leader rule: angles containing at least one of the leader's relevant creatives."""
    return _angles_result(
        _counts(leader, angle_input), 1, "None of the leader's ads are in an angle"
    )


def saturation(ads: list[Ad]) -> tuple[int, Saturation]:
    n = len({k for ad in ads if (k := ad.advertiser_key) is not None})
    for upper, label in SATURATION_BANDS:
        if n <= upper:
            return n, label
    return n, "crowded"
