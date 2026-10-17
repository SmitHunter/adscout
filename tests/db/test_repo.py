from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from adscout.db.repo import (
    create_snapshot,
    fail_snapshot,
    finish_snapshot,
    list_snapshots,
    load_ads,
    make_engine,
)
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.sources import SearchResult


def _ad(ad_id: str, page_id: str = "p1", page_name: str = "Shop", day: int = 1) -> Ad:
    return Ad(
        source="meta_web",
        ad_archive_id=ad_id,
        page_id=page_id,
        page_name=page_name,
        start_date=datetime(2026, 1, day, 12, 0, tzinfo=UTC),
        is_active=True,
        media=[MediaItem(type="image", url="https://x/y.jpg")],
        platforms=["facebook"],
        raw={"k": [1, 2]},
    )


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(tmp_path / "sub" / "adscout.db")
    with Session(engine) as s:
        yield s


def test_roundtrip_ads_preserve_utc(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="posture"), "meta_web")
    assert snap.id is not None and snap.status == "running"
    assert snap.started_at.tzinfo is not None
    finish_snapshot(session, snap.id, SearchResult(ads=[_ad("1")], status="ok"))
    session.expire_all()
    [ad] = load_ads(session, snap.id)
    assert ad.start_date.tzinfo is UTC
    assert ad.start_date == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert ad.media[0].url == "https://x/y.jpg"
    assert ad.platforms == ["facebook"]
    assert ad.raw == {"k": [1, 2]}
    assert session.get(type(snap), snap.id).finished_at.tzinfo is UTC  # type: ignore[union-attr]


def test_naive_datetime_rejected(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="posture"), "meta_web")
    assert snap.id is not None
    bad = _ad("1").model_copy(update={"start_date": datetime(2026, 1, 1)})
    with pytest.raises(ValueError, match="naive"):
        finish_snapshot(session, snap.id, SearchResult(ads=[bad], status="ok"))


def test_two_runs_create_two_snapshots(session: Session) -> None:
    q = SearchQuery(keyword="posture")
    create_snapshot(session, q, "meta_web")
    create_snapshot(session, q, "meta_web")
    create_snapshot(session, SearchQuery(keyword="other"), "meta_web")
    assert len(list_snapshots(session, "posture")) == 2
    assert len(list_snapshots(session)) == 3


def test_counts_set_on_finish(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="k"), "meta_web")
    assert snap.id is not None
    ads = [
        _ad("1", "p1", "A", 3),
        _ad("2", "p1", "A", 2),
        _ad("3", "", " B ", 1),
        _ad("4", "", "B", 1),
    ]
    done = finish_snapshot(session, snap.id, SearchResult(ads=ads, status="capped", message="cap"))
    assert (done.status, done.ad_count, done.advertiser_count) == ("capped", 4, 2)
    assert done.error_message == "cap"
    assert done.finished_at is not None
    assert [a.ad_archive_id for a in load_ads(session, snap.id)] == ["3", "4", "2", "1"]


def test_zero_ads_snapshot_ok(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="k"), "meta_web")
    assert snap.id is not None
    done = finish_snapshot(session, snap.id, SearchResult(status="ok"))
    assert (done.status, done.ad_count, done.advertiser_count) == ("ok", 0, 0)
    assert load_ads(session, snap.id) == []


def test_fail_snapshot_records_message(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="k"), "meta_web")
    assert snap.id is not None
    failed = fail_snapshot(session, snap.id, "boom")
    assert (failed.status, failed.error_message) == ("error", "boom")
    assert failed.finished_at is not None


def test_duplicate_ids_in_result_are_collapsed(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="k"), "meta_web")
    assert snap.id is not None
    ads = [_ad("1", "p1", "A"), _ad("1", "p2", "B"), _ad("2", "p3", "C")]
    done = finish_snapshot(session, snap.id, SearchResult(ads=ads, status="capped"))
    assert (done.status, done.ad_count, done.advertiser_count) == ("capped", 2, 2)
    loaded = load_ads(session, snap.id)
    assert len(loaded) == 2
    assert next(a for a in loaded if a.ad_archive_id == "1").page_id == "p1"


def test_unnamed_ad_not_counted_as_advertiser(session: Session) -> None:
    snap = create_snapshot(session, SearchQuery(keyword="k"), "meta_web")
    assert snap.id is not None
    ads = [_ad("1", "p1", "A"), _ad("2", "", "  ")]
    done = finish_snapshot(session, snap.id, SearchResult(ads=ads, status="ok"))
    assert (done.ad_count, done.advertiser_count) == (2, 1)
