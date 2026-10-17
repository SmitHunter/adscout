from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select

from adscout.db.repo import get_analysis, latest_analysis, make_engine, save_analysis
from adscout.db.tables import Analysis


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(tmp_path / "adscout.db")
    with Session(engine) as s:
        yield s


def _save(
    session: Session,
    snap: int = 1,
    h: str = "h1",
    result: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> Analysis:
    return save_analysis(
        session,
        snapshot_id=snap,
        prompt_version="angles-v1",
        model="m",
        backend="cli",
        input_hash=h,
        ad_count=5,
        result=result or {"v": 1},
        usage=usage,
    )


def test_analysis_cache_roundtrip_and_upsert(session: Session) -> None:
    assert get_analysis(session, 1, "angles-v1", "m", "h1") is None
    row = _save(session, usage={"input_tokens": 10})
    got = get_analysis(session, 1, "angles-v1", "m", "h1")
    assert got is not None
    assert got.id == row.id
    assert got.result == {"v": 1}
    assert got.usage == {"input_tokens": 10}
    assert got.created_at.tzinfo is not None

    again = _save(session, result={"v": 2}, usage=None)
    assert again.id == row.id
    assert len(list(session.exec(select(Analysis)))) == 1
    got2 = get_analysis(session, 1, "angles-v1", "m", "h1")
    assert got2 is not None
    assert got2.result == {"v": 2}
    assert got2.usage is None


def test_latest_analysis_newest_first(session: Session) -> None:
    assert latest_analysis(session, 1) is None
    old = _save(session, h="old")
    new = _save(session, h="new")
    _save(session, snap=2, h="x")
    old.created_at = datetime.now(UTC) - timedelta(days=1)
    session.add(old)
    session.commit()
    got = latest_analysis(session, 1)
    assert got is not None
    assert got.id == new.id
