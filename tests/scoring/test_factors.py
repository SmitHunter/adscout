from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from adscout.models import Ad
from adscout.scoring.creatives import Creative, group_creatives
from adscout.scoring.factors import (
    FactorResult,
    angles_pending,
    continuity,
    longevity,
    ramp,
    saturation,
    status,
    volume,
)
from adscout.scoring.thresholds import (
    CONTINUITY_MONTHS,
    VOLUME_CREATIVES,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
MEL = ZoneInfo("Australia/Melbourne")


def _ad(
    aid: str,
    start: datetime,
    *,
    active: bool = True,
    page: str = "p",
    end: datetime | None = None,
    cid: str | None = None,
) -> Ad:
    return Ad(
        source="meta_web",
        ad_archive_id=aid,
        page_id=page,
        page_name="P",
        start_date=start,
        end_date=end,
        is_active=active,
        body=f"body {aid}",
        collation_id=cid,
    )


def _creative(key: str, first_start: datetime) -> Creative:
    return Creative(key=key, ads=[], first_start=first_start, ad_count=1)


def test_ramp_anchor_points() -> None:
    v = VOLUME_CREATIVES
    assert ramp(2, v) == 0
    assert ramp(3, v) == 0.3
    assert ramp(10, v) == 0.7
    assert ramp(20, v) == 1.0
    assert ramp(6, v) == pytest.approx(0.3 + 0.4 * 3 / 7)
    c = CONTINUITY_MONTHS
    assert ramp(5, c) == 1.0
    assert ramp(4, c) == pytest.approx(0.65)


def test_status_edges() -> None:
    assert status(2, VOLUME_CREATIVES) == "fail"
    assert status(3, VOLUME_CREATIVES) == "warn"
    assert status(10, VOLUME_CREATIVES) == "pass"


def _lon(ads: list[Ad], now: datetime = NOW) -> FactorResult:
    return longevity(ads, group_creatives(ads), now)


def test_future_start_clamps_to_zero() -> None:
    res = _lon([_ad("1", NOW + timedelta(days=5))])
    assert res.value == 0
    assert res.status == "fail"


def test_no_active_ads_longevity_zero() -> None:
    old = NOW - timedelta(days=200)
    res = _lon([_ad("1", old, active=False, end=NOW - timedelta(days=1))])
    assert res.value == 0
    assert res.status == "fail"
    # its creative first started 200 days ago -> counts toward share
    assert res.score == pytest.approx(0.5)


def test_longevity_full_marks() -> None:
    res = _lon([_ad("1", NOW - timedelta(days=200))])
    assert res.value == 200
    assert res.status == "pass"
    assert res.score == 1.0
    assert res.points == 30.0
    assert res.max_points == 30
    assert res.detail == "100% of creatives running ≥ 90 days"


def test_longevity_share_counts_creatives_not_ads() -> None:
    old = NOW - timedelta(days=200)
    # One old creative run as three ads, plus three new single-ad creatives:
    # by ads 3/6 = 50% are >= 90 days, by creatives only 1/4 = 25%.
    ads = [_ad(f"o{i}", old, cid="X") for i in range(3)]
    ads += [_ad(f"n{i}", NOW - timedelta(days=10)) for i in range(3)]
    res = _lon(ads)
    assert res.detail == "25% of creatives running ≥ 90 days"
    assert res.score == pytest.approx(0.5 + 0.5 * 0.7)


def test_longevity_partial_share_scores_between() -> None:
    # 1 of 10 creatives >= 90 days: share 0.10 sits between fail 0.05 and pass 0.25.
    ads = [_ad("old", NOW - timedelta(days=200))]
    ads += [_ad(str(i), NOW - timedelta(days=10)) for i in range(9)]
    res = _lon(ads)
    assert res.score is not None
    share_part = (res.score - 0.5) / 0.5
    assert 0.3 < share_part < 0.7
    assert share_part == pytest.approx(0.3 + 0.4 * 0.05 / 0.20)


def test_longevity_warn_status() -> None:
    res = _lon([_ad("1", NOW - timedelta(days=60))])
    assert res.value == 60
    assert res.status == "warn"
    assert res.display == "60 days"


def test_continuity_counts_calendar_months_in_tz() -> None:
    # 2026-04-30T15:00Z is 2026-05-01 01:00 in Melbourne
    cs = [_creative("a", datetime(2026, 4, 30, 15, 0, tzinfo=UTC))]
    now = datetime(2026, 10, 17, tzinfo=UTC)
    res = continuity(cs, now, MEL)
    assert res.value == 1
    # window is May..Oct in Melbourne; in UTC April would be outside
    before = [_creative("b", datetime(2026, 4, 30, 10, 0, tzinfo=UTC))]
    assert continuity(before, now, MEL).value == 0


def test_continuity_window_crosses_year_boundary() -> None:
    now = datetime(2026, 2, 10, tzinfo=UTC)
    cs = [
        _creative("aug", datetime(2025, 8, 20, tzinfo=UTC)),  # outside Sep..Feb window
        _creative("sep", datetime(2025, 9, 3, tzinfo=UTC)),
        _creative("dec", datetime(2025, 12, 15, tzinfo=UTC)),
        _creative("feb", datetime(2026, 2, 1, tzinfo=UTC)),
    ]
    res = continuity(cs, now, ZoneInfo("UTC"))
    assert res.value == 3
    assert res.display == "3/6 months"


def test_continuity_future_in_current_month_counts() -> None:
    cs = [_creative("a", datetime(2026, 10, 25, tzinfo=UTC))]
    assert continuity(cs, NOW, ZoneInfo("UTC")).value == 1


def test_burst_launch_fails_continuity() -> None:
    ads = [_ad(str(i), datetime(2026, 8, 1 + i, tzinfo=UTC)) for i in range(6)]
    res = continuity(group_creatives(ads), NOW, ZoneInfo("UTC"))
    assert res.value == 1
    assert res.status == "fail"
    assert res.score == 0
    assert res.points == 0
    assert res.display == "1/6 months"


def test_continuity_five_of_six_passes() -> None:
    cs = [_creative(str(m), datetime(2026, m, 5, tzinfo=UTC)) for m in (6, 7, 8, 9, 10)]
    res = continuity(cs, NOW, ZoneInfo("UTC"))
    assert res.value == 5
    assert res.status == "pass"
    assert res.display == "5/6 months"
    assert res.points == 20.0


def test_volume() -> None:
    cs = [_creative(str(i), NOW) for i in range(10)]
    res = volume(cs)
    assert res.value == 10
    assert res.status == "pass"
    assert res.points == 17.5
    assert res.max_points == 25


def test_angles_pending() -> None:
    res = angles_pending()
    assert res.status == "pending"
    assert res.value is None and res.score is None and res.points is None
    assert res.display == "—"
    assert res.detail == "Needs angle analysis"
    assert res.max_points == 25


def _pages(n: int) -> list[Ad]:
    return [_ad(str(i), NOW, page=f"pg{i}") for i in range(n)]


def test_saturation_bands() -> None:
    assert saturation([]) == (0, "early")
    assert saturation(_pages(2)) == (2, "early")
    assert saturation(_pages(3)) == (3, "healthy")
    assert saturation(_pages(15)) == (15, "healthy")
    assert saturation(_pages(16)) == (16, "crowded")
