"""The demo data generator: deterministic, invented, and scored by the real scoring."""

import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
from sqlmodel import Session, col, select

from adscout.db.repo import list_watch_items, make_engine, snapshots_for_watch
from adscout.db.tables import AdRow, Alert, Analysis, Exclusion, Snapshot, WatchItem
from adscout.demo import DEMO_MARKER, generator
from adscout.demo.generator import (
    DEMO_BACKEND,
    DENYLIST,
    PRODUCTS,
    NotADemoFolder,
    demo_brand_names,
    denylist_hits,
    generate_demo,
    is_current,
    reset_demo,
)
from adscout.demo.images import Art, jpeg_bytes, phash, render
from adscout.models import MediaItem
from adscout.services.report import build_report
from adscout.services.watchlist import item_trend

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
TZ = ZoneInfo("Australia/Melbourne")
KEYWORDS = ("posture brace", "led face mask", "heated eye mask", "magnetic phone mount")


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data_dir = tmp_path_factory.mktemp("demo") / "data-demo"
    generate_demo(data_dir, seed=7, now=NOW)
    return data_dir


def _rows(data_dir: Path) -> dict[str, Any]:
    engine = make_engine(data_dir / "adscout.db")
    try:
        with Session(engine) as s:

            def dump(model: Any, *exclude: str) -> list[dict[str, Any]]:
                stmt = select(model).order_by(col(model.id))
                return [r.model_dump(exclude=set(exclude)) for r in s.exec(stmt)]

            return {
                "snapshots": dump(Snapshot),
                "ads": dump(AdRow),
                "analyses": dump(Analysis),
                "watch": dump(WatchItem),
                "alerts": dump(Alert),
                "exclusions": dump(Exclusion),
                "blobs": sorted(p.name for p in (data_dir / "media" / "blobs").iterdir()),
            }
    finally:
        engine.dispose()


def _latest(session: Session) -> dict[str, Snapshot]:
    """The newest snapshot of each keyword."""
    out: dict[str, Snapshot] = {}
    for snap in session.exec(select(Snapshot).order_by(col(Snapshot.started_at))):
        out[snap.query] = snap
    return out


def _fake_render(art: Art) -> Image.Image:
    """A cheap stand-in for the drawing: deterministic noise seeded by the art."""
    rng = random.Random(repr(art))
    img = Image.new("L", (32, 32))
    img.putdata([rng.randrange(256) for _ in range(32 * 32)])
    return img.convert("RGB")


@pytest.mark.slow
def test_generate_is_deterministic(
    demo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rows (ads, analyses, watch history, alerts) from two builds of the same seed match.
    # Drawing is swapped for cheap noise here; the drawing itself is checked below.
    monkeypatch.setattr(generator, "render", _fake_render)
    first, again = tmp_path / "first", tmp_path / "again"
    generate_demo(first, seed=7, now=NOW)
    generate_demo(again, seed=7, now=NOW)
    rows = _rows(first)
    assert _rows(again) == rows
    assert len(rows["blobs"]) > 100

    # Idempotent: a folder already built for this seed is left alone.
    db = demo_dir / "adscout.db"
    before = db.stat().st_mtime_ns
    assert is_current(demo_dir, 7, NOW)
    generate_demo(demo_dir, seed=7, now=NOW)
    assert db.stat().st_mtime_ns == before
    assert not is_current(demo_dir, 8, NOW)


def test_drawing_is_deterministic() -> None:
    """The same art always gives the same bytes and image hash (a sample of each product)."""
    for spec in PRODUCTS:
        brand = spec.brands[0]
        for layout in spec.layouts[:3]:
            art = Art(spec.art, layout, brand.name, brand.palette, "Stand taller", "40% OFF")
            a, b = render(art), render(art)
            assert jpeg_bytes(a) == jpeg_bytes(b)
            assert phash(a) == phash(b)


def test_brands_avoid_denylist(demo_dir: Path) -> None:
    names = demo_brand_names()
    assert len(names) == len(set(names)) >= 40
    engine = make_engine(demo_dir / "adscout.db")
    try:
        with Session(engine) as s:
            ads = list(s.exec(select(AdRow)))
    finally:
        engine.dispose()
    texts = {a.page_name for a in ads} | set(names)
    texts |= {f"{a.body} {a.title}" for a in ads}
    for text in texts:
        lowered = text.lower()
        assert not any(bad in lowered for bad in DENYLIST), text
    for ad in ads:
        assert ad.link_url is not None and ad.link_url.split("/")[2].endswith(".example")
        assert ad.link_caption is not None and ad.link_caption.endswith(".example")
        for raw in ad.media:
            item = MediaItem.model_validate(raw)
            assert item.url.startswith("https://media.example/")
            assert item.phash
            if item.type == "video":
                # Posters only: nothing plays.
                assert item.local_path is None and item.poster_path
                assert (demo_dir / "media" / item.poster_path).is_file()
            else:
                assert item.local_path and (demo_dir / "media" / item.local_path).is_file()


def test_denylist_hits_clean_demo(demo_dir: Path) -> None:
    """The check the screenshot script runs before publishing: the whole database is clean."""
    assert denylist_hits(demo_dir / "adscout.db") == []


def test_denylist_hits_finds_a_real_brand(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alert (id INTEGER, message TEXT, raw BLOB)")
        conn.execute("INSERT INTO alert VALUES (1, 'fine', ?)", (b"made by TheraBlink",))
        conn.execute("INSERT INTO alert VALUES (2, 'Yoli is up 30%', NULL)")
    conn.close()
    hits = denylist_hits(db)
    assert len(hits) == 2
    assert all(hit.startswith("alert: ") for hit in hits)


def test_scores_span_bands(demo_dir: Path) -> None:
    engine = make_engine(demo_dir / "adscout.db")
    try:
        with Session(engine) as s:
            latest = _latest(s)
            assert set(latest) == set(KEYWORDS)
            reports = {}
            for keyword, snap in latest.items():
                assert snap.id is not None
                reports[keyword] = build_report(s, snap.id, NOW, TZ)
    finally:
        engine.dispose()

    scores = {k: r.score.headline for k, r in reports.items()}
    assert any(v >= 70 for v in scores.values()), scores
    assert any(40 <= v <= 69 for v in scores.values()), scores
    assert any(v < 40 for v in scores.values()), scores
    assert {r.score.saturation for r in reports.values()} >= {"crowded", "early"}
    for keyword, r in reports.items():
        assert 40 <= r.snapshot.ad_count <= 180, keyword
        assert 6 <= r.snapshot.advertiser_count <= 30, keyword
        assert not r.score.provisional, keyword
        assert r.analysis is not None and r.analysis.backend == DEMO_BACKEND
        assert r.analysis.result.not_product_ad_ids, keyword  # a few ads flagged off-topic
        reasons = {a.reason for a in r.ads}
        assert "claude_off_topic" in reasons, keyword
    # Every exclusion kind shows somewhere: template ads, keyword misses, the user's own.
    reasons = {a.reason for r in reports.values() for a in r.ads}
    assert {"template", "no_keyword", "advertiser_override"} <= reasons
    # A few creatives share an image across ad variants.
    assert any(len({a.creative_key for a in r.ads}) < len(r.ads) for r in reports.values())


NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def test_verdicts_match_angle_counts(demo_dir: Path) -> None:
    """The verdict sits right under the rulers: its angle counts must agree with them."""
    engine = make_engine(demo_dir / "adscout.db")
    try:
        with Session(engine) as s:
            reports = {k: build_report(s, snap.id or 0, NOW, TZ) for k, snap in _latest(s).items()}
    finally:
        engine.dispose()
    for keyword, r in reports.items():
        assert r.analysis is not None
        verdict = r.analysis.result.verdict.lower()
        clusters = len(r.analysis.result.angles)
        assert verdict.startswith(f"{NUMBER_WORDS[clusters]} angles"), keyword
        assert r.score.leader is not None
        diversity = next(f for f in r.score.leader.factors if f.label == "Angle diversity")
        leader = int(diversity.value or 0)
        if leader != clusters:
            assert f"runs {NUMBER_WORDS[leader]}" in verdict or (
                f"runs only {NUMBER_WORDS[leader]}" in verdict
            ), keyword


def test_watch_history_has_capped_point_and_alerts(demo_dir: Path) -> None:
    engine = make_engine(demo_dir / "adscout.db")
    try:
        with Session(engine) as s:
            items = list_watch_items(s)
            assert len(items) == 2
            trends = {}
            for item in items:
                assert item.id is not None
                assert len(snapshots_for_watch(s, item.id)) == 21
                assert item.last_run_at is not None and item.last_status == "ok"
                trends[item.query] = item_trend(s, item.id, NOW, TZ)
            alerts = list(s.exec(select(Alert)))
    finally:
        engine.dispose()

    assert all(len(points) == 21 for points in trends.values())
    rising, falling = trends["posture brace"], trends["magnetic phone mount"]
    assert rising[-1].active_ads > rising[0].active_ads * 1.4
    assert falling[-1].active_ads < falling[0].active_ads * 0.75
    capped = [p for points in trends.values() for p in points if p.capped]
    assert len(capped) == 1

    assert len(alerts) == 2
    assert sum(1 for a in alerts if not a.read) == 1
    assert {a.kind for a in alerts} == {"change"}
    messages = sorted(a.message for a in alerts)
    assert "down" in messages[0] and "up" in messages[1]


def test_reset_demo_refuses_a_folder_that_is_not_demo_data(tmp_path: Path) -> None:
    folder = tmp_path / "data-demo"
    folder.mkdir()
    (folder / "adscout.db").write_text("real data")
    with pytest.raises(NotADemoFolder, match="isn't a demo folder"):
        reset_demo(folder)
    with pytest.raises(NotADemoFolder):
        generate_demo(folder, seed=7, now=NOW)
    assert (folder / "adscout.db").read_text() == "real data"


def test_reset_demo_wipes_a_marked_or_empty_folder(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    reset_demo(empty)
    marked = tmp_path / "marked"
    (marked / "media").mkdir(parents=True)
    (marked / DEMO_MARKER).write_text("{}")
    (marked / "adscout.db").write_text("demo")
    reset_demo(marked)
    assert list(marked.iterdir()) == []
