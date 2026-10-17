"""Start-date timeline of new creatives (pure)."""

from datetime import date, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from adscout.scoring.creatives import Creative


class TimelineBucket(BaseModel):
    start: date
    new_creatives: int
    ads: int


def _bucket_start(d: date, bucket: Literal["month", "week"]) -> date:
    if bucket == "month":
        return d.replace(day=1)
    return d - timedelta(days=d.weekday())


def _next(d: date, bucket: Literal["month", "week"]) -> date:
    if bucket == "week":
        return d + timedelta(days=7)
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def timeline(
    creatives: list[Creative], tz: ZoneInfo, bucket: Literal["month", "week"]
) -> list[TimelineBucket]:
    if not creatives:
        return []
    counts: dict[date, tuple[int, int]] = {}
    for c in creatives:
        key = _bucket_start(c.first_start.astimezone(tz).date(), bucket)
        n, ads = counts.get(key, (0, 0))
        counts[key] = (n + 1, ads + c.ad_count)
    out: list[TimelineBucket] = []
    cur, last = min(counts), max(counts)
    while cur <= last:
        n, ads = counts.get(cur, (0, 0))
        out.append(TimelineBucket(start=cur, new_creatives=n, ads=ads))
        cur = _next(cur, bucket)
    return out
