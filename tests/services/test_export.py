"""CSV building, file names, and the Playwright renderer (one real-browser test)."""

import asyncio
import csv
import io
import struct
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session

from adscout.db.repo import create_snapshot, finish_snapshot, make_engine
from adscout.models import Ad, SearchQuery
from adscout.models.ad import MediaItem
from adscout.services import export
from adscout.services.export import (
    CSV_COLUMNS,
    ExportPageFailed,
    escape_cell,
    export_filename,
    render_report,
    render_url,
    snapshot_csv,
)
from adscout.services.report import build_report
from adscout.sources import SearchResult

MELBOURNE = ZoneInfo("Australia/Melbourne")
NOW = datetime(2026, 10, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+61 400", "'+61 400"),
        ("-5", "'-5"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=cmd", "'\t=cmd"),
        ("\r=cmd", "'\r=cmd"),
        ("plain", "plain"),
        ("a=b", "a=b"),
        ("", ""),
    ],
)
def test_escape_cell(value: str, expected: str) -> None:
    assert escape_cell(value) == expected


@pytest.mark.parametrize(
    ("query", "sid", "fmt", "name"),
    [
        ("Posture Corrector", 7, "csv", "adscout-posture-corrector-7.csv"),
        ("  Ünïcode / Café!! ", 3, "pdf", "adscout-unicode-cafe-3.pdf"),
        ("???", 2, "png", "adscout-snapshot-2.png"),
        ("x" * 80, 1, "csv", f"adscout-{'x' * 40}-1.csv"),
    ],
)
def test_export_filename(query: str, sid: int, fmt: str, name: str) -> None:
    assert export_filename(query, sid, fmt) == name


def test_snapshot_csv_rows(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "db.sqlite")
    ads = [
        Ad(
            source="fake",
            ad_archive_id="1",
            page_id="p1",
            page_name="Shop",
            start_date=datetime(2026, 3, 4, 20, 0, tzinfo=UTC),  # 5 March in Melbourne
            end_date=datetime(2026, 4, 1, 1, 0, tzinfo=UTC),
            is_active=False,
            body="Posture corrector, line one\nline two",
            title="Fix your back",
            link_url="https://www.shop.example/p",
            collation_count=4,
            media=[
                MediaItem(type="image", url="https://x/1.jpg"),
                MediaItem(type="video", url="https://x/2.mp4"),
                MediaItem(type="image", url="https://x/3.jpg"),
            ],
        ),
        Ad(
            source="fake",
            ad_archive_id="2",
            page_id="p2",
            page_name="Other",
            start_date=datetime(2026, 3, 1, tzinfo=UTC),
            # Meta can report a stale end date on an ad that is still running.
            end_date=datetime(2026, 3, 5, tzinfo=UTC),
            is_active=True,
            body="Yoga mats",
        ),
    ]
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword="posture corrector"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=ads, status="ok"))
        report = build_report(s, snap.id, NOW, MELBOURNE)
    data = snapshot_csv(report, MELBOURNE)
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data[3:].decode("utf-8"), newline="")))
    assert tuple(rows[0]) == CSV_COLUMNS
    by_id = {r[0]: dict(zip(CSV_COLUMNS, r, strict=True)) for r in rows[1:]}
    one = by_id["1"]
    assert one == {
        "ad_archive_id": "1",
        "advertiser": "Shop",
        "page_id": "p1",
        "start_date": "2026-03-05",
        "end_date": "2026-04-01",
        "days_running": one["days_running"],
        "is_active": "false",
        "relevant": "true",
        "reason": "keyword",
        "creative_key": one["creative_key"],
        "collation_count": "4",
        "title": "Fix your back",
        "body": "Posture corrector, line one\nline two",
        "link_url": "https://www.shop.example/p",
        "landing_domain": "shop.example",
        "media_types": "image;video",
    }
    assert one["creative_key"] and int(one["days_running"]) > 0
    two = by_id["2"]
    assert (two["relevant"], two["reason"], two["media_types"]) == ("false", "no_keyword", "")
    # An active ad has no end date yet: days_running counts to now, not to Meta's stale date.
    assert (two["is_active"], two["end_date"]) == ("true", "")
    assert int(two["days_running"]) == (NOW - datetime(2026, 3, 1, tzinfo=UTC)).days
    engine.dispose()


async def test_render_report_opens_the_print_route(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    async def fake_render_url(url: str, fmt: str, *, timeout_s: float = 30) -> bytes:
        seen.append((url, fmt))
        return b"%PDF"

    monkeypatch.setattr(export, "render_url", fake_render_url)
    assert await render_report("http://127.0.0.1:8787/", 5, "pdf") == b"%PDF"
    assert await render_report("http://127.0.0.1:8787", 6, "png") == b"%PDF"
    assert seen == [
        ("http://127.0.0.1:8787/report/5/print", "pdf"),
        ("http://127.0.0.1:8787/report/6/print", "png"),
    ]


def _chromium_installed() -> bool:
    async def check() -> bool:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            if not Path(p.chromium.executable_path).is_file():
                return False
            browser = await p.chromium.launch()  # also catches missing system libraries
            await browser.close()
            return True

    try:
        return asyncio.run(check())
    except Exception:
        return False


async def test_render_report_real_playwright(tmp_path: Path) -> None:
    if not await asyncio.to_thread(_chromium_installed):
        pytest.skip("Chromium is not installed (uv run playwright install chromium)")
    page = tmp_path / "print.html"
    page.write_text(
        "<!doctype html><html><body><h1>AdScout</h1>"
        "<script>setTimeout(() => document.body.setAttribute('data-print-ready', ''), 50)"
        "</script></body></html>",
        encoding="utf-8",
    )
    pdf = await render_url(page.as_uri(), "pdf")
    assert pdf.startswith(b"%PDF")
    png = await render_url(page.as_uri(), "png")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width = struct.unpack(">I", png[16:20])[0]
    assert width == 1600


async def test_pdf_never_drops_content_that_overflows_a_page(tmp_path: Path) -> None:
    # The print page zooms its sheet to fit one A4 page; should that ever fall short
    # (long names, many advertisers), the overflow goes to a second page, never lost.
    if not await asyncio.to_thread(_chromium_installed):
        pytest.skip("Chromium is not installed (uv run playwright install chromium)")
    from pypdf import PdfReader

    page = tmp_path / "tall.html"
    rows = "".join(f"<p>Advertiser {n} with a long name</p>" for n in range(200))
    page.write_text(
        f"<!doctype html><html><body data-print-ready>{rows}<p>THE-END</p></body></html>",
        encoding="utf-8",
    )
    pdf = PdfReader(io.BytesIO(await render_url(page.as_uri(), "pdf")))
    assert len(pdf.pages) > 1
    assert "THE-END" in pdf.pages[-1].extract_text()


async def test_render_times_out_without_ready_marker(tmp_path: Path) -> None:
    if not await asyncio.to_thread(_chromium_installed):
        pytest.skip("Chromium is not installed (uv run playwright install chromium)")
    page = tmp_path / "never.html"
    page.write_text("<!doctype html><p>never ready</p>", encoding="utf-8")
    with pytest.raises(TimeoutError):
        await render_url(page.as_uri(), "pdf", timeout_s=1)


@pytest.mark.parametrize("reason", ["load", "not-found"])
async def test_render_fails_fast_on_failed_marker(tmp_path: Path, reason: str) -> None:
    if not await asyncio.to_thread(_chromium_installed):
        pytest.skip("Chromium is not installed (uv run playwright install chromium)")
    page = tmp_path / "failed.html"
    page.write_text(
        "<!doctype html><html><body><p>Can't load this report.</p>"
        f"<script>setTimeout(() => document.body.setAttribute('data-print-failed', '{reason}'), 50)"
        "</script></body></html>",
        encoding="utf-8",
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(ExportPageFailed) as caught:
        await render_url(page.as_uri(), "pdf")
    assert caught.value.reason == reason
    # Straight away: never the 30 s ready wait, and never a PDF of the error page.
    assert loop.time() - started < 10
