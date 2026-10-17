import hashlib
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import imagehash
import pytest
import respx
from PIL import Image
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import create_snapshot, finish_snapshot, load_ads, make_engine
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.services.media import MediaDownloader
from adscout.sources import CancelToken, SearchResult


def _png(color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    img = Image.new("RGB", (32, 32), color)
    for i in range(16):
        img.putpixel((i, i), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _blob(data: bytes, ext: str) -> str:
    return f"blobs/{hashlib.sha256(data).hexdigest()[:32]}.{ext}"


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


def _setup(tmp_path: Path, ads: list[Ad]) -> tuple[Engine, int]:
    engine = make_engine(tmp_path / "t.db")
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword="kw"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=ads, status="ok"))
        return engine, snap.id


class _Recorder:
    def __init__(self) -> None:
        self.gaps: list[float] = []

    async def sleep(self, d: float) -> None:
        self.gaps.append(d)


@respx.mock
async def test_downloads_images_and_posters_and_sets_local_path_and_phash(tmp_path: Path) -> None:
    png = _png()
    respx.get("https://cdn.test/a.png?sig=secret").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    respx.get("https://cdn.test/poster.jpg").respond(
        200, content=png, headers={"content-type": "image/jpeg"}
    )
    respx.get("https://cdn.test/v.mp4").respond(
        200, content=b"video-bytes", headers={"content-type": "video/mp4"}
    )
    ads = [
        _ad("1", [MediaItem(type="image", url="https://cdn.test/a.png?sig=secret")]),
        _ad(
            "2",
            [
                MediaItem(
                    type="video",
                    url="https://cdn.test/v.mp4",
                    preview_url="https://cdn.test/poster.jpg",
                )
            ],
        ),
    ]
    engine, sid = _setup(tmp_path, ads)
    media_dir = tmp_path / "media"
    progress: list[tuple[int, int]] = []
    dl = MediaDownloader(media_dir, sleep=_Recorder().sleep)
    saved = await dl.download_snapshot(engine, sid, lambda d, t: progress.append((d, t)), None)
    assert saved == 3
    assert progress[0] == (0, 3)
    assert progress[-1] == (3, 3)
    with Session(engine) as s:
        loaded = {a.ad_archive_id: a for a in load_ads(s, sid)}
    img = loaded["1"].media[0]
    assert img.local_path == _blob(png, "png")
    assert (media_dir / img.local_path).read_bytes() == png
    assert img.phash == str(imagehash.phash(Image.open(io.BytesIO(png))))
    vid = loaded["2"].media[0]
    assert vid.phash is not None
    assert vid.local_path is not None and vid.local_path.endswith(".mp4")
    assert (media_dir / vid.local_path).read_bytes() == b"video-bytes"


@respx.mock
async def test_skips_failed_and_oversize_without_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    png = _png()
    respx.get("https://cdn.test/ok.png").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    respx.get("https://cdn.test/gone.png?sig=SECRET").respond(404)
    respx.get("https://cdn.test/big.png").respond(
        200, content=b"x" * 2000, headers={"content-type": "image/png"}
    )
    respx.get("https://cdn.test/odd.bin").respond(
        200, content=b"x", headers={"content-type": "application/zip"}
    )
    respx.get("https://cdn.test/boom.png").mock(side_effect=httpx.ConnectError("no"))
    urls = ["ok.png", "gone.png?sig=SECRET", "big.png", "odd.bin", "boom.png"]
    ad = _ad("1", [MediaItem(type="image", url=f"https://cdn.test/{u}") for u in urls])
    engine, sid = _setup(tmp_path, [ad])
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep, max_bytes=1000)
    with caplog.at_level("WARNING"):
        saved = await dl.download_snapshot(engine, sid, None, None)
    assert saved == 1
    assert "SECRET" not in caplog.text
    with Session(engine) as s:
        media = load_ads(s, sid)[0].media
    assert [m.local_path is not None for m in media] == [True, False, False, False, False]


@respx.mock
async def test_sequential_with_gap_bounds(tmp_path: Path) -> None:
    png = _png()
    for i in range(3):
        respx.get(f"https://cdn.test/{i}.png").respond(
            200, content=png, headers={"content-type": "image/png"}
        )
    ad = _ad("1", [MediaItem(type="image", url=f"https://cdn.test/{i}.png") for i in range(3)])
    engine, sid = _setup(tmp_path, [ad])
    rec = _Recorder()
    dl = MediaDownloader(tmp_path / "media", sleep=rec.sleep)
    await dl.download_snapshot(engine, sid, None, None)
    assert len(rec.gaps) == 2
    assert all(0.3 <= g <= 0.8 for g in rec.gaps)


@respx.mock
async def test_existing_files_are_not_redownloaded(tmp_path: Path) -> None:
    png = _png()
    route = respx.get("https://cdn.test/a.png").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    ad = _ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])
    engine, sid = _setup(tmp_path, [ad])
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep)
    await dl.download_snapshot(engine, sid, None, None)
    await dl.download_snapshot(engine, sid, None, None)
    assert route.call_count == 1


@respx.mock
async def test_cancel_stops_download(tmp_path: Path) -> None:
    png = _png()
    route = respx.get(url__regex=r"https://cdn\.test/\d\.png").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    ad = _ad("1", [MediaItem(type="image", url=f"https://cdn.test/{i}.png") for i in range(3)])
    engine, sid = _setup(tmp_path, [ad])
    token = CancelToken()

    def on_progress(done: int, total: int) -> None:
        if done == 1:
            token.cancel()

    dl = MediaDownloader(tmp_path / "media")
    saved = await dl.download_snapshot(engine, sid, on_progress, token)
    assert saved == 1
    assert route.call_count == 1
    with Session(engine) as s:
        media = load_ads(s, sid)[0].media
    assert media[0].local_path is not None
    assert media[1].local_path is None


@respx.mock
async def test_video_saved_sets_poster_and_local_path(tmp_path: Path) -> None:
    respx.get("https://cdn.test/p.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    respx.get("https://cdn.test/v.mp4").respond(
        200, content=b"vid", headers={"content-type": "video/mp4"}
    )
    ad = _ad(
        "1",
        [
            MediaItem(
                type="video", url="https://cdn.test/v.mp4", preview_url="https://cdn.test/p.png"
            )
        ],
    )
    engine, sid = _setup(tmp_path, [ad])
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep)
    await dl.download_snapshot(engine, sid, None, None)
    with Session(engine) as s:
        m = load_ads(s, sid)[0].media[0]
    assert m.poster_path == _blob(_png(), "png")
    assert m.local_path == _blob(b"vid", "mp4")
    assert m.phash is not None


@respx.mock
async def test_video_over_cap_keeps_poster_only(tmp_path: Path) -> None:
    respx.get("https://cdn.test/p.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    respx.get("https://cdn.test/v.mp4").respond(
        200, content=b"x" * 5000, headers={"content-type": "video/mp4"}
    )
    ad = _ad(
        "1",
        [
            MediaItem(
                type="video", url="https://cdn.test/v.mp4", preview_url="https://cdn.test/p.png"
            )
        ],
    )
    engine, sid = _setup(tmp_path, [ad])
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep, max_bytes=4000)
    await dl.download_snapshot(engine, sid, None, None)
    with Session(engine) as s:
        m = load_ads(s, sid)[0].media[0]
    assert m.poster_path == _blob(_png(), "png")
    assert m.local_path is None


@respx.mock
async def test_hostile_ad_id_stays_inside_media_dir(tmp_path: Path) -> None:
    respx.get("https://cdn.test/a.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    ad = _ad("../x/[1]", [MediaItem(type="image", url="https://cdn.test/a.png")])
    engine, sid = _setup(tmp_path, [ad])
    media_dir = tmp_path / "media"
    await MediaDownloader(media_dir, sleep=_Recorder().sleep).download_snapshot(
        engine, sid, None, None
    )
    with Session(engine) as s:
        m = load_ads(s, sid)[0].media[0]
    assert m.local_path is not None
    target = (media_dir / m.local_path).resolve()
    assert target.is_file()
    assert target.parent == (media_dir / "blobs").resolve()
    assert not (tmp_path / "x").exists()


@respx.mock
async def test_chunked_oversize_leaves_no_file_or_part(tmp_path: Path) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 500

    respx.get("https://cdn.test/a.png").respond(
        200, content=chunks(), headers={"content-type": "image/png"}
    )
    ad = _ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])
    engine, sid = _setup(tmp_path, [ad])
    media_dir = tmp_path / "media"
    dl = MediaDownloader(media_dir, sleep=_Recorder().sleep, max_bytes=1000)
    assert await dl.download_snapshot(engine, sid, None, None) == 0
    assert list(media_dir.rglob("*")) == [] or all(p.is_dir() for p in media_dir.rglob("*"))


@respx.mock
async def test_phash_runs_off_the_event_loop_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    respx.get("https://cdn.test/a.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    engine, sid = _setup(
        tmp_path, [_ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])]
    )
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep)
    threads: list[int] = []
    real = dl._phash

    def spy(path: Path) -> str | None:
        threads.append(threading.get_ident())
        return real(path)

    monkeypatch.setattr(dl, "_phash", spy)
    assert await dl.download_snapshot(engine, sid, None, None) == 1
    assert threads and threads[0] != threading.get_ident()


@respx.mock
async def test_save_failure_with_failing_cleanup_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://cdn.test/a.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    engine, sid = _setup(
        tmp_path, [_ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])]
    )

    def fail(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "replace", fail)
    monkeypatch.setattr(Path, "unlink", fail)
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep)
    assert await dl.download_snapshot(engine, sid, None, None) == 0


@respx.mock
async def test_only_https_urls_are_fetched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plain = respx.get("http://cdn.test/a.png").respond(
        200, content=_png(), headers={"content-type": "image/png"}
    )
    urls = ["http://cdn.test/a.png", "file:///etc/passwd", "ftp://cdn.test/b.png"]
    ad = _ad("1", [MediaItem(type="image", url=u) for u in urls])
    engine, sid = _setup(tmp_path, [ad])
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep)
    with caplog.at_level("WARNING"):
        assert await dl.download_snapshot(engine, sid, None, None) == 0
    assert not plain.called
    assert "not https" in caplog.text
    with Session(engine) as s:
        assert all(m.local_path is None for m in load_ads(s, sid)[0].media)


@respx.mock
async def test_content_type_must_match_the_media_type(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    png = _png()
    # An image item answered with a video, and a video answered with an image.
    respx.get("https://cdn.test/img.png").respond(
        200, content=b"video-bytes", headers={"content-type": "video/mp4"}
    )
    respx.get("https://cdn.test/poster.jpg").respond(
        200, content=b"more-video", headers={"content-type": "video/mp4"}
    )
    respx.get("https://cdn.test/v.mp4").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    ads = [
        _ad("1", [MediaItem(type="image", url="https://cdn.test/img.png")]),
        _ad(
            "2",
            [
                MediaItem(
                    type="video",
                    url="https://cdn.test/v.mp4",
                    preview_url="https://cdn.test/poster.jpg",
                )
            ],
        ),
    ]
    engine, sid = _setup(tmp_path, ads)
    dl = MediaDownloader(tmp_path / "media", sleep=_Recorder().sleep)
    with caplog.at_level("WARNING"):
        assert await dl.download_snapshot(engine, sid, None, None) == 0
    assert "expected an image" in caplog.text and "expected a video" in caplog.text
    with Session(engine) as s:
        loaded = load_ads(s, sid)
    image, video = loaded[0].media[0], loaded[1].media[0]
    assert image.local_path is None
    assert video.local_path is None and video.poster_path is None


@respx.mock
async def test_part_file_name_is_unique_per_writer(tmp_path: Path) -> None:
    png = _png()
    respx.get("https://cdn.test/a.png").respond(
        200, content=png, headers={"content-type": "image/png"}
    )
    engine, sid = _setup(
        tmp_path, [_ad("1", [MediaItem(type="image", url="https://cdn.test/a.png")])]
    )
    media_dir = tmp_path / "media"
    blob = media_dir / _blob(png, "png")
    blob.parent.mkdir(parents=True)
    # Another process is mid-write of the same blob: its temporary file is left alone.
    foreign = blob.parent / f"{blob.name}.part"
    foreign.write_bytes(b"half")
    dl = MediaDownloader(media_dir, sleep=_Recorder().sleep)
    assert await dl.download_snapshot(engine, sid, None, None) == 1
    assert blob.read_bytes() == png
    assert foreign.read_bytes() == b"half"
    assert [p.name for p in blob.parent.iterdir() if p.name.endswith(".part")] == [foreign.name]
