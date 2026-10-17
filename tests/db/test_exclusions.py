from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session

from adscout.db.repo import clear_override, list_overrides, make_engine, set_override
from adscout.db.tables import Snapshot
from adscout.scoring.relevance import Override


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    with Session(make_engine(tmp_path / "adscout.db")) as s:
        yield s


def test_overrides_keyed_by_normalised_keyword(session: Session) -> None:
    set_override(session, "Posture Correctors", "ad", "a1", "exclude")
    assert list_overrides(session, "posture corrector") == [
        Override(scope="ad", target_id="a1", mode="exclude")
    ]
    assert list_overrides(session, "other") == []


def test_set_override_upserts_mode(session: Session) -> None:
    set_override(session, "kw", "advertiser", "p1", "exclude")
    set_override(session, "kw", "advertiser", "p1", "include")
    assert list_overrides(session, "kw") == [
        Override(scope="advertiser", target_id="p1", mode="include")
    ]


def test_clear_override(session: Session) -> None:
    set_override(session, "kw", "ad", "a1", "exclude")
    assert clear_override(session, "KW", "ad", "a1") is True
    assert clear_override(session, "kw", "ad", "a1") is False
    assert list_overrides(session, "kw") == []


def test_empty_keyword_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="no searchable words"):
        set_override(session, "!!!", "ad", "a1", "exclude")
    with pytest.raises(ValueError, match="no searchable words"):
        clear_override(session, "!!!", "ad", "a1")
    with pytest.raises(ValueError, match="no searchable words"):
        list_overrides(session, "!!!")


def test_keyword_raw_column_added_to_old_db(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE exclusion (id INTEGER NOT NULL PRIMARY KEY, keyword_norm VARCHAR NOT NULL,"
        " scope VARCHAR NOT NULL, target_id VARCHAR NOT NULL, mode VARCHAR NOT NULL,"
        " created_at DATETIME NOT NULL, UNIQUE (keyword_norm, scope, target_id))"
    )
    con.execute(
        "INSERT INTO exclusion VALUES (1, 'kw', 'ad', 'a1', 'exclude', '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    engine = make_engine(path)
    make_engine(path)  # idempotent

    with Session(engine) as s:
        assert list_overrides(s, "kw") == [Override(scope="ad", target_id="a1", mode="exclude")]
        set_override(s, "KW", "ad", "a2", "exclude")
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(exclusion)")]
    con.close()
    assert "keyword_raw" in cols


def test_set_override_stores_raw_keyword(session: Session) -> None:
    from sqlmodel import select

    from adscout.db.tables import Exclusion

    set_override(session, "Posture Correctors", "ad", "a1", "exclude")
    assert session.exec(select(Exclusion)).one().keyword_raw == "Posture Correctors"


def test_set_override_integrity_race_updates(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adscout.db import repo

    set_override(session, "kw", "ad", "a1", "exclude")
    real = repo._find_override
    calls = {"n": 0}

    def racy(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args, **kwargs)

    monkeypatch.setattr(repo, "_find_override", racy)
    set_override(session, "kw", "ad", "a1", "include")
    assert list_overrides(session, "kw") == [Override(scope="ad", target_id="a1", mode="include")]


def test_snapshot_keywords_normalised(session: Session) -> None:
    from adscout.db.repo import create_snapshot, snapshot_keywords
    from adscout.models import SearchQuery

    create_snapshot(session, SearchQuery(keyword="Posture Correctors"), "fake")
    assert snapshot_keywords(session) == {"posture corrector"}


def _adv_snapshot(session: Session, page_id: str, name: str) -> Snapshot:
    from adscout.db.repo import create_snapshot
    from adscout.models import SearchQuery

    return create_snapshot(
        session, SearchQuery(kind="advertiser", page_id=page_id, keyword=name), "fake"
    )


def test_override_key_for_snapshots(session: Session) -> None:
    from adscout.db.repo import create_snapshot, override_key
    from adscout.models import SearchQuery

    kw = create_snapshot(session, SearchQuery(keyword="Posture Correctors"), "fake")
    assert override_key(kw) == "posture corrector"
    assert override_key(_adv_snapshot(session, "123", "Nike")) == "page:123"


def test_keyword_and_advertiser_overrides_not_shared(session: Session) -> None:
    from adscout.db.repo import create_snapshot, override_key, snapshot_overrides
    from adscout.models import SearchQuery

    kw = create_snapshot(session, SearchQuery(keyword="nike"), "fake")
    adv = _adv_snapshot(session, "555", "Nike")
    set_override(session, override_key(kw), "ad", "a1", "exclude")
    set_override(session, override_key(adv), "ad", "a2", "exclude")
    assert snapshot_overrides(session, kw) == [Override(scope="ad", target_id="a1", mode="exclude")]
    assert snapshot_overrides(session, adv) == [
        Override(scope="ad", target_id="a2", mode="exclude")
    ]


def test_advertiser_without_words_can_have_overrides(session: Session) -> None:
    from adscout.db.repo import override_key, snapshot_overrides

    adv = _adv_snapshot(session, "777", "★★★")
    set_override(session, override_key(adv), "ad", "a1", "exclude")
    assert snapshot_overrides(session, adv) == [
        Override(scope="ad", target_id="a1", mode="exclude")
    ]
    assert list_overrides(session, "page:777") == snapshot_overrides(session, adv)
    assert clear_override(session, "page:777", "ad", "a1") is True
