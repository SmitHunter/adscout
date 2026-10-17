"""Compare and export endpoints (CSV in-process; PDF/PNG through a fake renderer)."""

import asyncio
import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import Session

from adscout.api.app import create_app
from adscout.config import Settings
from adscout.db.repo import create_snapshot, finish_snapshot, save_analysis
from adscout.models import Ad, SearchQuery
from adscout.services.export import CSV_COLUMNS, ExportError, ExportPageFailed
from adscout.sources import SearchResult

from .conftest import ANALYSIS_RESULT, KEYWORD, PNG_MAGIC, Env, FakeRenderer, seed

BOM = b"\xef\xbb\xbf"


def _rows(content: bytes) -> list[dict[str, str]]:
    assert content.startswith(BOM)
    return list(csv.DictReader(io.StringIO(content[len(BOM) :].decode("utf-8"))))


@pytest.mark.parametrize("ids", ["", "1", "1,2,3,4,5", "1,x", "1,1", "1,,2"])
async def test_compare_needs_2_to_4_ids(env: Env, ids: str) -> None:
    for _ in range(5):
        seed(env.engine)
    r = await env.client.get("/api/compare", params={"ids": ids})
    assert r.status_code == 422
    assert r.json() == {"detail": "Pick 2 to 4 different snapshots to compare."}


async def test_compare_unknown_snapshot_404(env: Env) -> None:
    sid = seed(env.engine)
    r = await env.client.get(f"/api/compare?ids={sid},999")
    assert r.status_code == 404
    assert r.json() == {"detail": "Snapshot 999 not found."}


async def test_compare_columns(env: Env) -> None:
    a = seed(env.engine)
    b = seed(env.engine, keyword="yoga mat")
    with Session(env.engine) as s:
        save_analysis(
            s,
            snapshot_id=a,
            prompt_version="v1",
            model="claude-sonnet-5-5",
            backend="cli",
            input_hash="h",
            ad_count=3,
            result=ANALYSIS_RESULT,
        )
    r = await env.client.get(f"/api/compare?ids={b},{a}")
    assert r.status_code == 200
    cols = r.json()
    assert [c["snapshot"]["id"] for c in cols] == [b, a]
    col = cols[1]
    assert set(col) == {
        "snapshot",
        "score",
        "market_score",
        "provisional",
        "relevant_ads",
        "advertisers",
        "creatives",
        "leader",
        "leader_factors",
        "market_factors",
        "timeline_month",
        "analysis",
    }
    report = (await env.client.get(f"/api/snapshots/{a}/report")).json()
    assert col["score"] == report["score"]["headline"]
    assert col["market_score"] == report["score"]["total"]
    assert col["leader"] == {
        k: report["score"]["leader"][k]
        for k in ("advertiser_key", "page_name", "ads", "creatives", "score")
    }
    assert col["leader_factors"] == report["score"]["leader"]["factors"]
    assert col["market_factors"] == report["score"]["factors"]
    assert col["timeline_month"] == report["timeline_month"]
    assert col["relevant_ads"] == 3 and col["advertisers"] == 2
    assert col["analysis"] == {"verdict": "One. Two. Three."}
    assert cols[0]["analysis"] is None


async def test_csv_header_bom_and_rows(env: Env) -> None:
    sid = seed(env.engine)
    r = await env.client.get(f"/api/snapshots/{sid}/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"] == (
        f'attachment; filename="adscout-posture-corrector-{sid}.csv"'
    )
    assert r.content.startswith(BOM)
    header = r.content[len(BOM) :].decode("utf-8").splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)
    assert CSV_COLUMNS == (
        "ad_archive_id",
        "advertiser",
        "page_id",
        "start_date",
        "end_date",
        "days_running",
        "is_active",
        "relevant",
        "reason",
        "creative_key",
        "collation_count",
        "title",
        "body",
        "link_url",
        "landing_domain",
        "media_types",
    )
    rows = {row["ad_archive_id"]: row for row in _rows(r.content)}
    assert set(rows) == {"1", "2", "3", "4"}
    assert rows["1"]["relevant"] == "true" and rows["1"]["reason"] == "keyword"
    assert rows["4"]["relevant"] == "false" and rows["4"]["reason"] == "no_keyword"
    assert rows["1"]["advertiser"] == "Alpha Store" and rows["1"]["page_id"] == "pid-alpha"
    # 2026-03-05 03:00 UTC is 14:00 the same day in Melbourne.
    assert rows["1"]["start_date"] == "2026-03-05" and rows["1"]["end_date"] == ""
    assert rows["1"]["is_active"] == "true"
    assert rows["1"]["body"] == "Best posture corrector, offer 1"


async def test_csv_escapes_formulas(env: Env) -> None:
    ad = Ad(
        source="fake",
        ad_archive_id="9",
        page_id="pid-evil",
        page_name='=HYPERLINK("http://x")',
        start_date=datetime(2026, 3, 5, tzinfo=UTC),
        is_active=True,
        title="@SUM(A1)",
        body="+posture corrector -1",
        link_url="-cmd",
    )
    with Session(env.engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=[ad], status="ok"))
    r = await env.client.get(f"/api/snapshots/{snap.id}/export.csv")
    [row] = _rows(r.content)
    assert row["advertiser"] == '\'=HYPERLINK("http://x")'
    assert row["title"] == "'@SUM(A1)"
    assert row["body"] == "'+posture corrector -1"
    assert row["link_url"] == "'-cmd"


async def test_csv_unknown_404(env: Env) -> None:
    r = await env.client.get("/api/snapshots/999/export.csv")
    assert r.status_code == 404


@pytest.mark.parametrize(
    ("fmt", "media_type", "magic"),
    [("pdf", "application/pdf", b"%PDF"), ("png", "image/png", PNG_MAGIC)],
)
async def test_export_rendered(env: Env, fmt: str, media_type: str, magic: bytes) -> None:
    sid = seed(env.engine)
    r = await env.client.get(f"/api/snapshots/{sid}/export.{fmt}")
    assert r.status_code == 200
    assert r.headers["content-type"] == media_type
    assert r.headers["content-disposition"] == (
        f'attachment; filename="adscout-posture-corrector-{sid}.{fmt}"'
    )
    assert r.content.startswith(magic)
    # The server's own address, never the request's Host.
    assert env.renderer.calls == [("http://127.0.0.1:8787", sid, fmt)]


@pytest.mark.parametrize(
    ("fail", "code", "detail"),
    [
        (TimeoutError(), 504, "Export timed out."),
        (ExportError("Chromium is not installed."), 503, "Chromium is not installed."),
    ],
)
async def test_export_failures(env: Env, fail: Exception, code: int, detail: str) -> None:
    sid = seed(env.engine)
    env.renderer.fail = fail
    r = await env.client.get(f"/api/snapshots/{sid}/export.pdf")
    assert r.status_code == code
    assert r.json() == {"detail": detail}


@pytest.mark.parametrize("fmt", ["pdf", "png"])
async def test_export_print_page_failures_are_mapped(env: Env, fmt: str) -> None:
    sid = seed(env.engine)
    env.renderer.fail = ExportPageFailed("not-found")
    r = await env.client.get(f"/api/snapshots/{sid}/export.{fmt}")
    assert r.status_code == 404
    assert r.json() == {"detail": f"Snapshot {sid} not found."}

    env.renderer.fail = ExportPageFailed("load")
    r = await env.client.get(f"/api/snapshots/{sid}/export.{fmt}")
    assert r.status_code == 502
    assert r.json() == {"detail": "The report could not be loaded for export."}


async def test_export_unknown_404_never_renders(env: Env) -> None:
    for fmt in ("pdf", "png"):
        r = await env.client.get(f"/api/snapshots/999/export.{fmt}")
        assert r.status_code == 404
    assert env.renderer.calls == []


async def test_openapi_has_m5_operations(env: Env) -> None:
    schema: dict[str, Any] = (await env.client.get("/openapi.json")).json()
    ids = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert {
        "listWatchItems",
        "createWatchItem",
        "updateWatchItem",
        "deleteWatchItem",
        "runWatchItem",
        "getWatchTrend",
        "listAlerts",
        "markAlertsRead",
        "compareSnapshots",
        "exportCsv",
        "exportPdf",
        "exportPng",
    } <= ids


@pytest.mark.parametrize("path", ["export.pdf", "export.png", "export.csv", "report"])
async def test_cross_site_get_blocked_by_fetch_metadata(env: Env, path: str) -> None:
    sid = seed(env.engine)
    r = await env.client.get(
        f"/api/snapshots/{sid}/{path}", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "Cross-site request blocked."}
    assert env.renderer.calls == []


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none", None])
async def test_own_and_direct_requests_pass(env: Env, site: str | None) -> None:
    sid = seed(env.engine)
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    r = await env.client.get(f"/api/snapshots/{sid}/export.pdf", headers=headers)
    assert r.status_code == 200
    r = await env.client.get("/api/watch", headers=headers)
    assert r.status_code == 200


async def test_cross_site_navigation_to_spa_still_works(env: Env) -> None:
    (env.static_dir / "index.html").write_text("<html>shell</html>")
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        static_dir=env.static_dir,
        allowed_hosts=["test"],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/report/1", headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 200 and r.text == "<html>shell</html>"


async def test_concurrent_exports_one_409(env: Env) -> None:
    sid = seed(env.engine)
    env.renderer.gate = asyncio.Event()
    first = asyncio.create_task(env.client.get(f"/api/snapshots/{sid}/export.pdf"))
    await asyncio.wait_for(env.renderer.started.wait(), 2)
    second = await env.client.get(f"/api/snapshots/{sid}/export.png")
    assert second.status_code == 409
    assert second.json() == {"detail": "Another export is in progress."}
    env.renderer.gate.set()
    assert (await first).status_code == 200
    assert len(env.renderer.calls) == 1
    # The slot is free again.
    r = await env.client.get(f"/api/snapshots/{sid}/export.png")
    assert r.status_code == 200


async def test_export_base_url_uses_the_bound_port(env: Env, tmp_path: Path) -> None:
    sid = seed(env.engine)
    renderer = FakeRenderer()
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        render_report=renderer,
        port=9123,
        allowed_hosts=["localhost"],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:5173") as c:
        r = await c.get(f"/api/snapshots/{sid}/export.pdf")
    assert r.status_code == 200
    assert renderer.calls == [("http://127.0.0.1:9123", sid, "pdf")]


@pytest.mark.parametrize(
    ("host", "code"), [("test", 400), ("testserver", 400), ("127.0.0.1:8787", 200)]
)
async def test_production_hosts_are_loopback_only(tmp_path: Path, host: str, code: int) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    app = create_app(settings, static_dir=tmp_path / "none")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
        r = await client.get("/api/watch")
    assert r.status_code == code
    app.state.api.engine.dispose()
