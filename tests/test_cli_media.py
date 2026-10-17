import io
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from PIL import Image
from sqlalchemy import Engine
from sqlmodel import Session
from typer.testing import CliRunner

import adscout.cli as cli
from adscout.cli import app
from adscout.config import Settings
from adscout.db.repo import create_snapshot, finish_snapshot, load_ads, make_engine
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.services.media import MediaDownloader
from adscout.sources import SearchResult


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (40, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def _ad(aid: str, media: list[MediaItem]) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=aid,
        page_id="p",
        page_name="P",
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        is_active=True,
        media=media,
    )


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = Settings(_env_file=None, data_dir=tmp_path / "data")
    monkeypatch.setattr(cli, "get_settings", lambda: s)

    async def no_sleep(_: float) -> None:
        return None

    # The real gaps are 0.3-0.8 s; tests don't wait.
    monkeypatch.setattr(cli, "_downloader", lambda d: MediaDownloader(d, sleep=no_sleep))
    return s


@pytest.fixture
def engine(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Engine:
    settings.data_dir.mkdir(parents=True)
    eng = make_engine(settings.db_path)
    monkeypatch.setattr(cli, "get_engine", lambda: eng)
    return eng


def _seed(engine: Engine, ads: list[Ad]) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword="kw"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=ads, status="ok"))
        return snap.id


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "120"})
    return result.exit_code, result.output


@respx.mock
def test_media_downloads_snapshot_and_reports_saved_and_skipped(
    engine: Engine, settings: Settings
) -> None:
    respx.get("https://cdn.test/a.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    respx.get("https://cdn.test/gone.png").respond(403)
    sid = _seed(
        engine,
        [
            _ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")]),
            _ad("2", [MediaItem(type="image", url="https://cdn.test/gone.png")]),
        ],
    )

    code, out = _invoke("media", str(sid))

    assert code == 0, out
    assert "Downloaded 2/2" in out
    assert "Saved 1 of 2 media files for snapshot 1 (1 skipped)." in out
    with Session(engine) as s:
        ads = {ad.ad_archive_id: ad for ad in load_ads(s, sid)}
    local = ads["1"].media[0].local_path
    assert local is not None and re.fullmatch(r"blobs/[0-9a-f]{32}\.png", local)
    assert (settings.media_dir / local).is_file()
    assert ads["2"].media[0].local_path is None


def test_media_unknown_snapshot_exits_1(engine: Engine) -> None:
    code, out = _invoke("media", "99")

    assert code == 1
    assert "Snapshot 99 not found. Run: uv run adscout snapshots" in out


def test_media_snapshot_without_media(engine: Engine) -> None:
    sid = _seed(engine, [_ad("1", [])])

    code, out = _invoke("media", str(sid))

    assert code == 0, out
    assert f"Snapshot {sid} has no media to download." in out
