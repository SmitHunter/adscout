import io
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import imagehash
from PIL import Image, ImageDraw

from adscout.models import Ad, MediaItem
from adscout.scoring import creative_key, group_creatives
from adscout.sources.meta_web.parse_graphql import parse_payloads

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meta_web" / "posture-corrector-US"


def _ad(
    aid: str,
    *,
    body: str = "Hello",
    url: str | None = "https://cdn.test/a/b.jpg",
    cid: str | None = None,
    count: int = 1,
    day: int = 1,
) -> Ad:
    media = [MediaItem(type="image", url=url)] if url else []
    return Ad(
        source="meta_web",
        ad_archive_id=aid,
        page_id="p",
        page_name="P",
        start_date=datetime(2026, 1, day, tzinfo=UTC),
        is_active=True,
        body=body,
        media=media,
        collation_id=cid,
        collation_count=count,
    )


def test_collation_id_groups() -> None:
    ads = [_ad("1", cid="X", body="a"), _ad("2", cid="X", body="b", url=None)]
    out = group_creatives(ads)
    assert len(out) == 1
    assert out[0].key == "c:X"


def test_same_text_and_media_path_group_despite_cdn_query() -> None:
    a = _ad("1", body="Hi  There", url="https://cdn.test/a/b.jpg?_nc_ohc=111")
    b = _ad("2", body="hi there", url="https://cdn.test/a/b.jpg?_nc_ohc=222")
    assert len(group_creatives([a, b])) == 1
    assert creative_key(a).startswith("t:")


def test_different_media_split() -> None:
    a = _ad("1", url="https://cdn.test/a/b.jpg")
    b = _ad("2", url="https://cdn.test/a/c.jpg")
    assert len(group_creatives([a, b])) == 2


def test_empty_text_and_no_media_never_merge() -> None:
    a = _ad("1", body="", url=None)
    b = _ad("2", body="   ", url=None)
    out = group_creatives([a, b])
    assert len(out) == 2
    assert {c.key for c in out} == {"a:1", "a:2"}


def test_ad_count_sums_collation_counts() -> None:
    out = group_creatives([_ad("1", cid="X", count=3), _ad("2", cid="X", count=1)])
    assert out[0].ad_count == 4


def test_duplicate_ad_counted_once() -> None:
    out = group_creatives([_ad("1", cid="X", count=3), _ad("1", cid="X", count=3)])
    assert out[0].ad_count == 3
    assert len(out[0].ads) == 1


def test_first_start_is_min() -> None:
    out = group_creatives([_ad("1", cid="X", day=5), _ad("2", cid="X", day=2)])
    assert out[0].first_start == datetime(2026, 1, 2, tzinfo=UTC)


def test_sorted_by_first_start_then_key() -> None:
    out = group_creatives(
        [_ad("1", cid="B", day=3), _ad("2", cid="A", day=3), _ad("3", cid="C", day=1)]
    )
    assert [c.key for c in out] == ["c:C", "c:A", "c:B"]


def test_fixture_creatives_le_ads() -> None:
    texts = [(FIXTURE / "page.html").read_text(encoding="utf-8")] + [
        p.read_text(encoding="utf-8") for p in sorted(FIXTURE.glob("graphql_*.json"))
    ]
    ads = parse_payloads(texts)
    assert len(ads) == 69
    creatives = group_creatives(ads)
    assert 0 < len(creatives) <= 69


def _pattern(kind: str) -> Image.Image:
    img = Image.new("RGB", (128, 128), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    if kind == "shapes":
        draw.ellipse((10, 20, 70, 90), fill=(200, 30, 30))
        draw.rectangle((60, 60, 120, 110), fill=(20, 40, 160))
        draw.polygon([(90, 5), (125, 40), (70, 45)], fill=(30, 140, 50))
    else:
        for i in range(0, 128, 16):
            draw.rectangle((i, 0, i + 7, 127), fill=(0, 0, 0))
    return img


def _jpeg_phash(kind: str, quality: int) -> str:
    buf = io.BytesIO()
    _pattern(kind).save(buf, "JPEG", quality=quality)
    return str(imagehash.phash(Image.open(io.BytesIO(buf.getvalue()))))


def _hashed(aid: str, body: str, phash: str | None, day: int = 1) -> Ad:
    ad = _ad(aid, body=body, url=f"https://cdn.test/{aid}.jpg", day=day)
    ad.media[0].phash = phash
    return ad


def test_phash_near_duplicates_merge() -> None:
    a = _hashed("1", "one", _jpeg_phash("shapes", 95), day=2)
    b = _hashed("2", "two", _jpeg_phash("shapes", 30), day=1)
    out = group_creatives([a, b])
    assert len(out) == 1
    assert {x.ad_archive_id for x in out[0].ads} == {"1", "2"}
    assert out[0].first_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert out[0].ad_count == 2
    assert out[0].key == min(creative_key(a), creative_key(b))


def test_distinct_images_do_not_merge() -> None:
    a = _hashed("1", "one", _jpeg_phash("shapes", 90))
    b = _hashed("2", "two", _jpeg_phash("stripes", 90))
    assert len(group_creatives([a, b])) == 2


def test_no_phash_never_merges_by_hash() -> None:
    a = _hashed("1", "one", None)
    b = _hashed("2", "two", None)
    c = _hashed("3", "three", _jpeg_phash("shapes", 90))
    assert len(group_creatives([a, b, c])) == 3


def test_mismatched_or_invalid_phash_never_merges() -> None:
    a = _hashed("1", "one", "ffffffffffffffff")
    b = _hashed("2", "two", "ffffffffffffffffff")  # longer hash, same leading bits
    c = _hashed("3", "three", "not-hex!")
    d = _hashed("4", "four", "zzzzzzzzzzzzzzzz")
    assert len(group_creatives([a, b, c, d])) == 4


def test_phash_merge_matches_imagehash_distance() -> None:
    base = int(_jpeg_phash("shapes", 90), 16)
    near = f"{base ^ 0b111111:016x}"  # 6 bits flipped: still a match
    far = f"{base ^ 0b1111111:016x}"  # 7 bits flipped: not a match
    a = _hashed("1", "one", f"{base:016x}")
    assert len(group_creatives([a, _hashed("2", "two", near)])) == 1
    assert len(group_creatives([a, _hashed("3", "three", far)])) == 2


def test_group_creatives_1000_distinct_phashes_is_fast() -> None:
    rng = random.Random(42)
    ads = [_hashed(str(i), f"body {i}", f"{rng.getrandbits(64):016x}") for i in range(1000)]
    started = time.perf_counter()
    out = group_creatives(ads)
    elapsed = time.perf_counter() - started
    assert len(out) == 1000
    assert elapsed < 0.5, f"group_creatives took {elapsed:.2f}s"
