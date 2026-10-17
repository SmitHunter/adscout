"""Watchlist views reuse each snapshot's scored summary until something it depends on
changes (overrides, analysis, the hour, the time zone)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import (
    create_snapshot,
    create_watch_item,
    finish_snapshot,
    list_watch_items,
    make_engine,
    save_analysis,
    set_override,
    update_ad_media,
)
from adscout.db.tables import Snapshot
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.services import watchlist
from adscout.services.report import build_report
from adscout.sources import SearchResult

NOW = datetime(2026, 10, 15, 9, 10, tzinfo=UTC)
TZ = ZoneInfo("UTC")
KEYWORD = "posture corrector"


def _ads() -> list[Ad]:
    return [
        Ad(
            source="fake",
            ad_archive_id=str(n),
            page_id=f"pid-{n % 2}",
            page_name=f"Shop {n % 2}",
            start_date=datetime(2026, 3, 1, tzinfo=UTC) + timedelta(days=n),
            is_active=True,
            body=f"Best posture corrector {n}",
        )
        for n in range(4)
    ]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _seed(engine: Engine) -> tuple[int, int]:
    q = SearchQuery(keyword=KEYWORD, country="US")
    with Session(engine) as s:
        item = create_watch_item(s, q)
        snap = create_snapshot(s, q, "fake", watch_item_id=item.id)
        assert snap.id is not None and item.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=_ads(), status="ok"))
        return item.id, snap.id


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def spy(session: Session, snapshot_id: int, *args: object, **kwargs: object) -> object:
        calls.append(snapshot_id)
        return build_report(session, snapshot_id, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("adscout.services.watchlist.build_report", spy)
    watchlist.clear_summary_cache()
    return calls


def _trend(engine: Engine, item_id: int, now: datetime = NOW, tz: ZoneInfo = TZ) -> int:
    with Session(engine) as s:
        points = watchlist.item_trend(s, item_id, now, tz)
    return points[0].active_ads


def test_repeat_requests_reuse_the_summary(engine: Engine, builds: list[int]) -> None:
    item_id, sid = _seed(engine)
    first = _trend(engine, item_id)
    assert _trend(engine, item_id, NOW + timedelta(minutes=30)) == first
    with Session(engine) as s:
        item = list_watch_items(s)[0]
        watchlist.watch_item_views(s, [item], NOW, TZ, next_run=None, running_ids=set())
    assert builds == [sid]


def test_override_change_rescores(engine: Engine, builds: list[int]) -> None:
    item_id, sid = _seed(engine)
    assert _trend(engine, item_id) == 4
    with Session(engine) as s:
        set_override(s, KEYWORD, "advertiser", "pid-0", "exclude")
    assert _trend(engine, item_id) == 2
    assert builds == [sid, sid]


def test_new_analysis_hour_and_timezone_rescore(engine: Engine, builds: list[int]) -> None:
    item_id, sid = _seed(engine)
    _trend(engine, item_id)
    with Session(engine) as s:
        save_analysis(
            s,
            snapshot_id=sid,
            prompt_version="v",
            model="m",
            backend="cli",
            input_hash="h",
            ad_count=1,
            result={},
        )
    _trend(engine, item_id)
    _trend(engine, item_id, NOW + timedelta(hours=1))
    _trend(engine, item_id, NOW + timedelta(hours=1), ZoneInfo("Australia/Melbourne"))
    assert builds == [sid] * 4


def test_cache_is_per_database(tmp_path: Path, builds: list[int]) -> None:
    one = make_engine(tmp_path / "one.db")
    two = make_engine(tmp_path / "two.db")
    item_one, _ = _seed(one)
    item_two, _ = _seed(two)
    _trend(one, item_one)
    _trend(two, item_two)
    assert len(builds) == 2


def test_media_written_after_the_search_rescores(engine: Engine, builds: list[int]) -> None:
    # Two creatives (different copy and images) that turn out to share one image once
    # the media phase stores their phashes: the report then merges them.
    q = SearchQuery(keyword=KEYWORD, country="US")
    ads = [
        Ad(
            source="fake",
            ad_archive_id=str(n),
            page_id="pid-0",
            page_name="Shop 0",
            start_date=datetime(2026, 3, 1, tzinfo=UTC),
            is_active=True,
            body=f"Best posture corrector, version {n}",
            media=[MediaItem(type="image", url=f"https://cdn.test/{n}.jpg")],
        )
        for n in range(2)
    ]
    with Session(engine) as s:
        snap = create_snapshot(s, q, "fake")
        assert snap.id is not None
        sid = snap.id
        finish_snapshot(s, sid, SearchResult(ads=ads, status="ok"))

    def creatives() -> int:
        with Session(engine) as s:
            row = s.get(Snapshot, sid)
            assert row is not None
            [summary] = watchlist._summaries(s, [row], NOW, TZ)
        return len(summary.creatives)

    assert creatives() == 2
    with Session(engine) as s:
        for ad in ads:
            media = [ad.media[0].model_copy(update={"phash": "ffff0000ffff0000"})]
            update_ad_media(s, sid, ad.ad_archive_id, media)
    assert creatives() == 1
    assert builds == [sid, sid]
