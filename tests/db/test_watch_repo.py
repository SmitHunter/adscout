"""Watch items, their snapshots and alerts."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session

from adscout.db.repo import (
    add_alert,
    create_snapshot,
    create_watch_item,
    delete_watch_item,
    get_watch_item,
    list_alerts,
    list_watch_items,
    make_engine,
    mark_read,
    snapshots_for_watch,
    update_watch_item,
)
from adscout.db.tables import Snapshot
from adscout.models import SearchQuery


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    with Session(make_engine(tmp_path / "adscout.db")) as s:
        yield s


KW = SearchQuery(keyword="posture corrector", country="AU")
ADV = SearchQuery(kind="advertiser", page_id="1234567", keyword="Alpha Store")


def test_create_watch_item_keyword_and_advertiser(session: Session) -> None:
    kw = create_watch_item(session, KW)
    adv = create_watch_item(session, ADV)
    assert (kw.kind, kw.query, kw.page_id, kw.country) == (
        "keyword",
        "posture corrector",
        None,
        "AU",
    )
    assert (kw.active_status, kw.media_type, kw.enabled) == ("active", "all", True)
    assert kw.last_run_at is None and kw.last_status is None
    assert (adv.kind, adv.query, adv.page_id, adv.country) == (
        "advertiser",
        "Alpha Store",
        "1234567",
        "ALL",
    )
    assert [w.id for w in list_watch_items(session)] == [kw.id, adv.id]


def test_create_watch_item_is_idempotent(session: Session) -> None:
    first = create_watch_item(session, KW)
    again = create_watch_item(session, KW)
    assert again.id == first.id
    assert len(list_watch_items(session)) == 1


def test_update_and_enabled_filter(session: Session) -> None:
    item = create_watch_item(session, KW)
    assert item.id is not None
    ran = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    update_watch_item(session, item.id, enabled=False, last_run_at=ran, last_status="ok")
    got = get_watch_item(session, item.id)
    assert got is not None
    assert (got.enabled, got.last_run_at, got.last_status) == (False, ran, "ok")
    assert list_watch_items(session, enabled_only=True) == []


def test_snapshots_for_watch_oldest_first(session: Session) -> None:
    item = create_watch_item(session, KW)
    assert item.id is not None
    a = create_snapshot(session, KW, "fake", watch_item_id=item.id)
    create_snapshot(session, KW, "fake")  # manual search: not the item's
    b = create_snapshot(session, KW, "fake", watch_item_id=item.id)
    assert [s.id for s in snapshots_for_watch(session, item.id)] == [a.id, b.id]


def test_delete_watch_keeps_snapshots_removes_alerts(session: Session) -> None:
    item = create_watch_item(session, KW)
    other = create_watch_item(session, ADV)
    assert item.id is not None and other.id is not None
    snap = create_snapshot(session, KW, "fake", watch_item_id=item.id)
    assert snap.id is not None
    add_alert(session, watch_item_id=item.id, snapshot_id=snap.id, kind="change", message="m")
    kept = add_alert(session, watch_item_id=other.id, snapshot_id=None, kind="blocked", message="b")

    assert delete_watch_item(session, item.id) is True
    assert delete_watch_item(session, item.id) is False

    assert get_watch_item(session, item.id) is None
    row = session.get(Snapshot, snap.id)
    assert row is not None and row.watch_item_id is None
    assert [a.id for a in list_alerts(session)] == [kept.id]


def test_alerts_mark_read(session: Session) -> None:
    item = create_watch_item(session, KW)
    assert item.id is not None
    a1 = add_alert(
        session,
        watch_item_id=item.id,
        snapshot_id=None,
        kind="change",
        message="up",
        change_pct=40.0,
    )
    a2 = add_alert(session, watch_item_id=item.id, snapshot_id=None, kind="change", message="dn")
    a3 = add_alert(session, watch_item_id=item.id, snapshot_id=None, kind="blocked", message="b")
    assert a1.read is False and a1.change_pct == 40.0 and a1.created_at.tzinfo is not None
    # Newest first.
    assert [a.id for a in list_alerts(session)] == [a3.id, a2.id, a1.id]

    assert mark_read(session, [a1.id or 0]) == 1
    assert [a.id for a in list_alerts(session, unread_only=True)] == [a3.id, a2.id]
    assert mark_read(session, "all") == 2
    assert list_alerts(session, unread_only=True) == []
    assert len(list_alerts(session)) == 3


def test_snapshot_stores_kind_and_page_id(session: Session) -> None:
    snap = create_snapshot(session, ADV, "fake")
    assert (snap.kind, snap.page_id, snap.query, snap.country) == (
        "advertiser",
        "1234567",
        "Alpha Store",
        "ALL",
    )


def test_page_id_column_added_to_old_database(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    engine = make_engine(db)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE snapshot DROP COLUMN page_id"))
    engine = make_engine(db)
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(snapshot)"))}
    assert "page_id" in cols
