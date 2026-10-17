"""Media is stored once in content-addressed blobs, shared across snapshots."""

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import respx
from PIL import Image
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import create_snapshot, finish_snapshot, load_ads, make_engine
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.services.media import MediaDownloader
from adscout.sources import SearchResult


def _png() -> bytes:
    img = Image.new("RGB", (32, 32), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
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


def _snapshot(engine: Engine, ads: list[Ad]) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword="kw"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=ads, status="ok"))
        return snap.id


async def _no_sleep(_: float) -> None:
    return None


def _blob_name(data: bytes, ext: str) -> str:
    return f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"


@respx.mock
async def test_same_url_path_downloads_once_across_snapshots(tmp_path: Path) -> None:
    png = _png()
    # Different hosts and signatures, same CDN path: the same file.
    first = respx.get("https://cdn-a.test/v/t1/abc.png?sig=1").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    second = respx.get("https://cdn-b.test/v/t1/abc.png?sig=2").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    engine = make_engine(tmp_path / "t.db")
    media_dir = tmp_path / "media"
    s1 = _snapshot(
        engine, [_ad("1", [MediaItem(type="image", url="https://cdn-a.test/v/t1/abc.png?sig=1")])]
    )
    s2 = _snapshot(
        engine, [_ad("1", [MediaItem(type="image", url="https://cdn-b.test/v/t1/abc.png?sig=2")])]
    )
    dl = MediaDownloader(media_dir, sleep=_no_sleep)
    assert await dl.download_snapshot(engine, s1, None, None) == 1
    assert await dl.download_snapshot(engine, s2, None, None) == 1
    assert first.call_count + second.call_count == 1

    name = _blob_name(png, "png")
    with Session(engine) as s:
        paths = [load_ads(s, sid)[0].media[0].local_path for sid in (s1, s2)]
        phash = load_ads(s, s2)[0].media[0].phash
    assert paths == [f"blobs/{name}", f"blobs/{name}"]
    assert phash is not None
    assert (media_dir / "blobs" / name).read_bytes() == png
    assert not (media_dir / str(s1)).exists()
    index = json.loads((media_dir / "index.json").read_text())
    assert index == {"/v/t1/abc.png": name}


@respx.mock
async def test_identical_content_different_paths_one_blob(tmp_path: Path) -> None:
    png = _png()
    for p in ("one.png", "two.png"):
        respx.get(f"https://cdn.test/{p}").respond(
            200, content=png, headers={"content-type": "image/png"}
        )
    engine = make_engine(tmp_path / "t.db")
    media_dir = tmp_path / "media"
    sid = _snapshot(
        engine,
        [
            _ad("1", [MediaItem(type="image", url="https://cdn.test/one.png")]),
            _ad("2", [MediaItem(type="image", url="https://cdn.test/two.png")]),
        ],
    )
    dl = MediaDownloader(media_dir, sleep=_no_sleep)
    assert await dl.download_snapshot(engine, sid, None, None) == 2
    blobs = sorted(p.name for p in (media_dir / "blobs").iterdir())
    assert blobs == [_blob_name(png, "png")]
    with Session(engine) as s:
        assert {a.media[0].local_path for a in load_ads(s, sid)} == {f"blobs/{blobs[0]}"}


@respx.mock
async def test_corrupt_index_is_rebuilt(tmp_path: Path) -> None:
    png = _png()
    route = respx.get("https://cdn.test/a.png").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "index.json").write_text("{not json")
    engine = make_engine(tmp_path / "t.db")
    sid = _snapshot(engine, [_ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])])
    dl = MediaDownloader(media_dir, sleep=_no_sleep)
    assert await dl.download_snapshot(engine, sid, None, None) == 1
    assert route.call_count == 1
    assert json.loads((media_dir / "index.json").read_text()) == {"/a.png": _blob_name(png, "png")}
    assert not list(media_dir.glob("*.tmp"))


@respx.mock
async def test_index_entry_with_missing_blob_redownloads(tmp_path: Path) -> None:
    png = _png()
    route = respx.get("https://cdn.test/a.png").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "index.json").write_text(json.dumps({"/a.png": "deadbeef.png"}))
    engine = make_engine(tmp_path / "t.db")
    sid = _snapshot(engine, [_ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])])
    dl = MediaDownloader(media_dir, sleep=_no_sleep)
    assert await dl.download_snapshot(engine, sid, None, None) == 1
    assert route.call_count == 1


@respx.mock
async def test_legacy_per_snapshot_file_is_kept(tmp_path: Path) -> None:
    route = respx.get("https://cdn.test/a.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    engine = make_engine(tmp_path / "t.db")
    sid = _snapshot(engine, [_ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])])
    media_dir = tmp_path / "media"
    legacy = media_dir / str(sid) / "1_0.png"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(_png())
    dl = MediaDownloader(media_dir, sleep=_no_sleep)
    await dl.download_snapshot(engine, sid, None, None)
    assert route.call_count == 0
    with Session(engine) as s:
        assert load_ads(s, sid)[0].media[0].local_path == f"{sid}/1_0.png"
