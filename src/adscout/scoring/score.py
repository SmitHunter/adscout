"""Assemble the Winner Score (pure: `now` and `tz` are parameters)."""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, computed_field

from adscout.models import Ad, SearchKind
from adscout.scoring.creatives import Creative, group_creatives
from adscout.scoring.factors import (
    AngleInput,
    FactorResult,
    Saturation,
    angles,
    angles_pending,
    continuity,
    leader_angles,
    longevity,
    saturation,
    volume,
)
from adscout.scoring.relevance import Override, RelevanceDecision, decide_relevance


class LeaderStats(BaseModel):
    """The strongest single advertiser, scored on its own relevant ads with the same formula."""

    advertiser_key: str
    page_name: str
    ads: int
    creatives: int
    score: int
    factors: list[FactorResult]


class WinnerScore(BaseModel):
    total: int
    provisional: bool
    factors: list[FactorResult]
    leader: LeaderStats | None
    advertisers: int
    saturation: Saturation
    relevant_ads: int
    excluded_ads: int
    creatives: int
    warnings: list[str]
    decisions: dict[str, RelevanceDecision]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def headline(self) -> int:
        """Headline Winner Score: the strongest advertiser's score (pooled `total` if none)."""
        return self.leader.score if self.leader is not None else self.total


def _score_group(
    ads: list[Ad],
    now: datetime,
    tz: ZoneInfo,
    angle_factor: Callable[[list[Ad]], FactorResult] | None,
) -> tuple[list[FactorResult], int, list[Creative]]:
    """Factors and 0-100 total for a set of relevant ads; pending factors are left out.

    ``angle_factor`` scores the angles for these ads; None leaves angles pending.
    """
    creatives = group_creatives(ads)
    factors = [
        longevity(ads, creatives, now),
        continuity(creatives, now, tz),
        volume(creatives),
        angles_pending() if angle_factor is None else angle_factor(ads),
    ]
    active = [f for f in factors if f.status != "pending"]
    weight = sum(f.max_points for f in active)
    earned = sum(f.max_points * (f.score or 0.0) for f in active)
    total = round(100 * earned / weight) if weight else 0
    return factors, total, creatives


class _CreativeAngles:
    """Angles keyed by creative: each lead id Claude returned stands for its whole group.

    Groups come from ``group_creatives`` over all the snapshot's ads, so an angle keeps a
    creative while any ad of that group is relevant (e.g. after its lead is excluded).
    """

    def __init__(self, ads: list[Ad], angle_input: AngleInput) -> None:
        groups = group_creatives(ads)
        self.key_of = {m.ad_archive_id: c.key for c in groups for m in c.ads}
        flagged = {self.key_of[i] for i in angle_input.not_product if i in self.key_of}
        # Every ad of each creative Claude flagged as not selling the product.
        self.off_topic = frozenset(
            m.ad_archive_id for c in groups if c.key in flagged for m in c.ads
        )
        self.keyed = AngleInput(
            angles=[
                (name, list(dict.fromkeys(self.key_of[i] for i in ids if i in self.key_of)))
                for name, ids in angle_input.angles
            ]
        )

    def keys(self, ads: list[Ad]) -> set[str]:
        return {self.key_of[a.ad_archive_id] for a in ads if a.ad_archive_id in self.key_of}

    def market(self, ads: list[Ad]) -> FactorResult:
        return angles(self.keys(ads), self.keyed)

    def leader(self, ads: list[Ad]) -> FactorResult:
        return leader_angles(self.keys(ads), self.keyed)


def _leader(
    relevant: list[Ad], now: datetime, tz: ZoneInfo, by_creative: _CreativeAngles | None
) -> LeaderStats | None:
    """Advertiser with the most creatives; ties -> most ads -> smallest key."""
    by_key: dict[str, list[Ad]] = {}
    for ad in relevant:
        if (key := ad.advertiser_key) is not None:
            by_key.setdefault(key, []).append(ad)
    if not by_key:
        return None
    counted = [(len(group_creatives(group)), len(group), key) for key, group in by_key.items()]
    n_creatives, n_ads, key = min(counted, key=lambda t: (-t[0], -t[1], t[2]))
    group = by_key[key]
    angle_factor = None if by_creative is None else by_creative.leader
    factors, total, _ = _score_group(group, now, tz, angle_factor)
    first = group[0]
    return LeaderStats(
        advertiser_key=key,
        page_name=first.page_name.strip() or first.page_id or key,
        ads=n_ads,
        creatives=n_creatives,
        score=total,
        factors=factors,
    )


def off_topic_warning(n: int) -> str:
    """The score warning for ``n`` ads excluded because Claude marked them off-topic."""
    if n == 1:
        return (
            "Claude marked 1 ad as not selling this product. It's excluded from the score. "
            "Review it in the Excluded tab."
        )
    return (
        f"Claude marked {n} ads as not selling this product. They're excluded from the score. "
        "Review them in the Excluded tab."
    )


def score_ads(
    ads: list[Ad],
    keyword: str,
    overrides: list[Override],
    now: datetime,
    tz: ZoneInfo,
    angle_input: AngleInput | None = None,
    kind: SearchKind = "keyword",
) -> WinnerScore:
    """Score the relevant ads. Provisional (angles pending) only when ``angle_input`` is None.

    ``angle_input`` holds ad ids (the analysis's creative leads); angles count creatives.
    Its ``not_product`` creatives are excluded (reason ``claude_off_topic``) unless a user
    override covers the ad or its advertiser. ``kind="advertiser"`` (``keyword`` is the
    advertiser's name) skips the keyword filter: every other ad is relevant.
    """
    by_creative = None if angle_input is None else _CreativeAngles(ads, angle_input)
    off_topic = frozenset() if by_creative is None else by_creative.off_topic
    decisions = decide_relevance(ads, keyword, overrides, off_topic=off_topic, kind=kind)
    relevant = [ad for ad in ads if decisions[ad.ad_archive_id].relevant]
    angle_factor = None if by_creative is None else by_creative.market
    factors, total, creatives = _score_group(relevant, now, tz, angle_factor)
    advertisers, sat = saturation(relevant)

    warnings: list[str] = []
    if not relevant:
        warnings.append("No relevant ads — check the keyword or your exclusions.")
    elif sat == "crowded":
        warnings.append(f"Crowded: {advertisers} advertisers — proven demand, hard to stand out.")
    elif sat == "early":
        warnings.append(f"Early: only {advertisers} advertiser(s) — demand not yet proven.")
    flagged = sum(1 for d in decisions.values() if d.reason == "claude_off_topic")
    if flagged:
        warnings.append(off_topic_warning(flagged))

    return WinnerScore(
        total=total,
        provisional=any(f.status == "pending" for f in factors),
        factors=factors,
        leader=_leader(relevant, now, tz, by_creative),
        advertisers=advertisers,
        saturation=sat,
        relevant_ads=len(relevant),
        excluded_ads=len(ads) - len(relevant),
        creatives=len(creatives),
        warnings=warnings,
        decisions=decisions,
    )
