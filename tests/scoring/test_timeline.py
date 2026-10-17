from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from adscout.scoring import Creative, TimelineBucket, timeline

UTC_TZ = ZoneInfo("UTC")


def _c(key: str, start: datetime, ads: int = 1) -> Creative:
    return Creative(key=key, ads=[], first_start=start, ad_count=ads)


def test_monthly_timeline_contiguous_with_gaps() -> None:
    cs = [
        _c("a", datetime(2026, 1, 5, tzinfo=UTC), 2),
        _c("b", datetime(2026, 1, 20, tzinfo=UTC), 1),
        _c("c", datetime(2026, 4, 2, tzinfo=UTC), 3),
    ]
    out = timeline(cs, UTC_TZ, "month")
    assert out == [
        TimelineBucket(start=date(2026, 1, 1), new_creatives=2, ads=3),
        TimelineBucket(start=date(2026, 2, 1), new_creatives=0, ads=0),
        TimelineBucket(start=date(2026, 3, 1), new_creatives=0, ads=0),
        TimelineBucket(start=date(2026, 4, 1), new_creatives=1, ads=3),
    ]


def test_weekly_buckets_start_monday() -> None:
    # 2026-10-17 is a Saturday; its ISO week starts Monday 2026-10-12.
    cs = [
        _c("a", datetime(2026, 10, 17, tzinfo=UTC)),
        _c("b", datetime(2026, 10, 26, tzinfo=UTC)),
    ]
    out = timeline(cs, UTC_TZ, "week")
    assert [b.start for b in out] == [date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]
    assert all(b.start.weekday() == 0 for b in out)
    assert [b.new_creatives for b in out] == [1, 0, 1]


def test_timeline_uses_tz_and_empty() -> None:
    assert timeline([], UTC_TZ, "month") == []
    mel = ZoneInfo("Australia/Melbourne")
    out = timeline([_c("a", datetime(2026, 2, 28, 20, tzinfo=UTC))], mel, "month")
    assert out[0].start == date(2026, 3, 1)


def test_monthly_timeline_crosses_year_boundary() -> None:
    cs = [_c("a", datetime(2025, 11, 20, tzinfo=UTC)), _c("b", datetime(2026, 2, 3, tzinfo=UTC))]
    out = timeline(cs, UTC_TZ, "month")
    assert [b.start for b in out] == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert [b.new_creatives for b in out] == [1, 0, 0, 1]
