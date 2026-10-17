import asyncio
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session

from adscout.api.app import create_app
from adscout.config import Settings
from adscout.db.repo import create_snapshot, list_overrides, make_engine
from adscout.db.tables import Snapshot
from adscout.models import SearchQuery
from adscout.services.searches import SearchManager

from .conftest import KEYWORD, TEST_HOSTS, Env, seed, wait_idle

BUSY = "A search is already running. Wait for it to finish or cancel it."


async def test_startup_sweeps_running_snapshots(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    engine = make_engine(settings.db_path)
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword="x"), "fake")
        sid = snap.id
    app = create_app(settings, engine=engine, static_dir=tmp_path / "none")
    async with app.router.lifespan_context(app):
        with Session(engine) as s:
            row = s.get(Snapshot, sid)
            assert row is not None and row.status == "error"


async def test_startup_skips_sweep_while_profile_is_busy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from adscout.locks import profile_lock

    settings = Settings(_env_file=None, data_dir=tmp_path)
    engine = make_engine(settings.db_path)
    with Session(engine) as s:
        sid = create_snapshot(s, SearchQuery(keyword="x"), "fake").id
        queued = create_snapshot(s, SearchQuery(keyword="y"), "fake", status="queued").id
    held = profile_lock(settings.browser_profile_dir.parent, timeout_s=0)
    held.acquire()
    try:
        app = create_app(settings, engine=engine, static_dir=tmp_path / "none")
        with caplog.at_level("INFO", logger="adscout.api.app"):
            async with app.router.lifespan_context(app):
                with Session(engine) as s:
                    row = s.get(Snapshot, sid)
                    # A CLI scrape may own this "running" snapshot: left alone.
                    assert row is not None and row.status == "running"
                    # Only a server queues searches, and this is the only server: a
                    # "queued" row is always stale and always swept.
                    waiting = s.get(Snapshot, queued)
                    assert waiting is not None and waiting.status == "error"
    finally:
        held.release()
    assert "sweep" in caplog.text.lower()
    # The startup check released the lock it took: it is free again.
    again = profile_lock(settings.browser_profile_dir.parent, timeout_s=0)
    again.acquire()
    again.release()


async def test_startup_sweep_releases_profile_lock(tmp_path: Path) -> None:
    from adscout.locks import profile_lock

    settings = Settings(_env_file=None, data_dir=tmp_path)
    app = create_app(settings, engine=make_engine(settings.db_path), static_dir=tmp_path / "none")
    async with app.router.lifespan_context(app):
        lock = profile_lock(settings.browser_profile_dir.parent, timeout_s=0)
        lock.acquire()  # would raise filelock.Timeout if the startup kept it
        lock.release()


async def test_shutdown_cancels_running_search(env: Env) -> None:
    r = await env.client.post("/api/searches", json={"keyword": "x"})
    sid = r.json()["snapshot_id"]
    await env.source.started.wait()
    await env.manager.shutdown()
    assert env.manager.active() is None
    with Session(env.engine) as s:
        row = s.get(Snapshot, sid)
        assert row is not None and row.status == "cancelled"


async def test_snapshots_newest_first_and_keyword_filter(env: Env) -> None:
    first = seed(env.engine)
    second = seed(env.engine, keyword="yoga mat")
    r = await env.client.get("/api/snapshots")
    assert r.status_code == 200
    rows = r.json()
    assert [row["id"] for row in rows] == [second, first]
    assert rows[0]["query"] == "yoga mat" and rows[0]["ad_count"] == 4
    assert rows[0]["started_at"].endswith("Z")

    r = await env.client.get("/api/snapshots", params={"keyword": KEYWORD})
    assert [row["id"] for row in r.json()] == [first]


async def test_start_search_returns_201_and_active(env: Env) -> None:
    assert (await env.client.get("/api/searches/active")).json() is None
    r = await env.client.post("/api/searches", json={"keyword": "posture corrector"})
    assert r.status_code == 201
    sid = r.json()["snapshot_id"]
    await env.source.started.wait()
    active = (await env.client.get("/api/searches/active")).json()
    assert active["snapshot_id"] == sid and active["kind"] == "progress"
    env.source.gate.set()
    await wait_idle(env.manager)


async def test_start_search_busy_409(env: Env) -> None:
    await env.client.post("/api/searches", json={"keyword": "a"})
    r = await env.client.post("/api/searches", json={"keyword": "b"})
    assert r.status_code == 409
    assert r.json() == {"detail": BUSY}
    env.source.gate.set()
    await wait_idle(env.manager)


async def test_start_search_invalid_422(env: Env) -> None:
    r = await env.client.post("/api/searches", json={"keyword": "x", "country": "ZZ"})
    assert r.status_code == 422
    assert "unknown country code" in r.text
    r = await env.client.post("/api/searches", json={"keyword": "  "})
    assert r.status_code == 422


async def test_start_search_unsupported_422(env: Env) -> None:
    r = await env.client.post("/api/searches", json={"keyword": "x", "country": "EU"})
    assert r.status_code == 422
    assert r.json() == {"detail": "EU searches are only available through the official API."}
    assert env.manager.active() is None


async def test_cancel(env: Env) -> None:
    sid = (await env.client.post("/api/searches", json={"keyword": "x"})).json()["snapshot_id"]
    await env.source.started.wait()
    r = await env.client.post(f"/api/searches/{sid + 1}/cancel")
    assert r.status_code == 200 and r.json() == {"cancelled": False}
    r = await env.client.post(f"/api/searches/{sid}/cancel")
    assert r.json() == {"cancelled": True}
    await wait_idle(env.manager)
    r = await env.client.post("/api/searches/abc/cancel")
    assert r.status_code == 422


async def test_report(env: Env) -> None:
    sid = seed(env.engine)
    r = await env.client.get(f"/api/snapshots/{sid}/report")
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot"]["id"] == sid
    assert len(body["ads"]) == 4
    assert {a["key"] for a in body["advertisers"]} >= {"pid-alpha", "pid-beta"}


async def test_report_404(env: Env) -> None:
    r = await env.client.get("/api/snapshots/999/report")
    assert r.status_code == 404
    assert r.json() == {"detail": "Snapshot 999 not found."}


async def test_threshold_serialised_as_pass(env: Env) -> None:
    sid = seed(env.engine)
    body = (await env.client.get(f"/api/snapshots/{sid}/report")).json()
    threshold = body["score"]["factors"][0]["threshold"]
    assert "pass" in threshold and "pass_" not in threshold
    schema = (await env.client.get("/openapi.json")).json()
    props = schema["components"]["schemas"]["Threshold"]["properties"]
    assert "pass" in props and "pass_" not in props


async def test_openapi_operation_ids(env: Env) -> None:
    schema = (await env.client.get("/openapi.json")).json()
    ids = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert {
        "getSettings",
        "listSnapshots",
        "startSearch",
        "getActiveSearch",
        "searchEvents",
        "cancelSearch",
        "getReport",
        "setOverride",
        "clearOverride",
        "openBrowser",
        "updateSettings",
        "startAnalysis",
        "getAnalysis",
    } <= ids


async def test_overrides_put_and_delete(env: Env) -> None:
    body = {"keyword": KEYWORD, "scope": "advertiser", "target_id": "pid-alpha"}
    r = await env.client.put("/api/overrides", json={**body, "mode": "exclude"})
    assert r.status_code == 204
    with Session(env.engine) as s:
        [o] = list_overrides(s, KEYWORD)
        assert (o.scope, o.target_id, o.mode) == ("advertiser", "pid-alpha", "exclude")
    r = await env.client.request("DELETE", "/api/overrides", json=body)
    assert r.status_code == 204
    with Session(env.engine) as s:
        assert list_overrides(s, KEYWORD) == []


async def test_overrides_by_snapshot_id(env: Env) -> None:
    from adscout.db.repo import override_key, snapshot_overrides

    with Session(env.engine) as s:
        kw = create_snapshot(s, SearchQuery(keyword="nike"), "fake")
        adv = create_snapshot(
            s, SearchQuery(kind="advertiser", page_id="555", keyword="Nike"), "fake"
        )
        kw_id, adv_id = kw.id, adv.id
    body = {"snapshot_id": adv_id, "scope": "ad", "target_id": "a1"}
    r = await env.client.put("/api/overrides", json={**body, "mode": "exclude"})
    assert r.status_code == 204
    with Session(env.engine) as s:
        adv_row, kw_row = s.get(Snapshot, adv_id), s.get(Snapshot, kw_id)
        assert adv_row is not None and kw_row is not None
        assert override_key(adv_row) == "page:555"
        assert [o.target_id for o in snapshot_overrides(s, adv_row)] == ["a1"]
        assert snapshot_overrides(s, kw_row) == []
    r = await env.client.request("DELETE", "/api/overrides", json=body)
    assert r.status_code == 204
    with Session(env.engine) as s:
        assert list_overrides(s, "page:555") == []


async def test_overrides_snapshot_errors(env: Env) -> None:
    base = {"scope": "ad", "target_id": "a1", "mode": "exclude"}
    r = await env.client.put("/api/overrides", json={**base, "snapshot_id": 999})
    assert r.status_code == 404
    r = await env.client.put("/api/overrides", json=base)
    assert r.status_code == 422
    # The deprecated keyword form only covers keyword searches.
    r = await env.client.put("/api/overrides", json={**base, "keyword": "page:555"})
    assert r.status_code == 422


async def test_overrides_need_exactly_one_of_snapshot_id_or_keyword(env: Env) -> None:
    sid = seed(env.engine)
    both = {"snapshot_id": sid, "keyword": KEYWORD, "scope": "ad", "target_id": "1"}
    r = await env.client.put("/api/overrides", json={**both, "mode": "exclude"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Send snapshot_id or keyword, not both."
    r = await env.client.request("DELETE", "/api/overrides", json=both)
    assert r.status_code == 422
    with Session(env.engine) as s:
        assert list_overrides(s, KEYWORD) == []


async def test_overrides_invalid_422(env: Env) -> None:
    r = await env.client.put(
        "/api/overrides",
        json={"keyword": KEYWORD, "scope": "page", "target_id": "x", "mode": "exclude"},
    )
    assert r.status_code == 422
    r = await env.client.put(
        "/api/overrides",
        json={"keyword": "!!!", "scope": "ad", "target_id": "x", "mode": "exclude"},
    )
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


async def test_browser_open_202(env: Env) -> None:
    r = await env.client.post("/api/browser/open")
    assert r.status_code == 202
    for _ in range(100):
        if env.opened:
            break
        await asyncio.sleep(0.01)
    assert env.opened == ["opened"]


async def test_browser_open_409_while_searching(env: Env) -> None:
    await env.client.post("/api/searches", json={"keyword": "x"})
    r = await env.client.post("/api/browser/open")
    assert r.status_code == 409
    assert env.opened == []
    env.source.gate.set()
    await wait_idle(env.manager)


async def test_browser_open_409_while_profile_locked(env: Env) -> None:
    from adscout.locks import PROFILE_BUSY_MESSAGE, acquire_profile, profile_lock

    env.settings.data_dir.mkdir(parents=True, exist_ok=True)
    held = profile_lock(env.settings.data_dir)
    acquire_profile(held)
    try:
        r = await env.client.post("/api/browser/open")
    finally:
        held.release()
    assert r.status_code == 409
    assert r.json() == {"detail": PROFILE_BUSY_MESSAGE}
    assert env.opened == []


def _media(env: Env, sid: int, name: str, data: bytes = b"img") -> Path:
    d = env.settings.media_dir / str(sid)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(data)
    return p


async def test_media_serves_file_with_long_cache(env: Env) -> None:
    _media(env, 3, "123_0.jpg", b"jpegbytes")
    r = await env.client.get("/media/3/123_0.jpg")
    assert r.status_code == 200
    assert r.content == b"jpegbytes"
    assert "immutable" in r.headers["cache-control"]
    assert r.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize(
    "path",
    [
        "/media/3/..%2f..%2fadscout.db",
        "/media/3/..%5c..%5cadscout.db",
        "/media/3/%2e%2e",
        "/media/3/.hidden",
        "/media/3/C:%5cWindows%5cwin.ini",
        "/media/3/%2fetc%2fpasswd",
        "/media/3/a b.jpg",
        "/media/abc/123_0.jpg",
        "/media/-1/123_0.jpg",
        "/media/3/missing.jpg",
        "/media/4/123_0.jpg",
        "/media/3/sub",
    ],
)
async def test_media_route_blocks_traversal(env: Env, path: str) -> None:
    _media(env, 3, "123_0.jpg")
    _media(env, 3, ".hidden")
    (env.settings.media_dir / "3" / "sub").mkdir()
    env.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    r = await env.client.get(path)
    assert r.status_code == 404


async def test_spa_fallback_serves_index(env: Env) -> None:
    (env.static_dir / "index.html").write_text("<html>shell</html>")
    (env.static_dir / "assets").mkdir()
    (env.static_dir / "assets" / "app.js").write_text("console.log(1)")
    (env.static_dir / "favicon.svg").write_text("<svg/>")
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        static_dir=env.static_dir,
        allowed_hosts=TEST_HOSTS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/", "/report/12", "/some/deep/link"):
            r = await client.get(path)
            assert r.status_code == 200, path
            assert r.text == "<html>shell</html>"
        r = await client.get("/assets/app.js")
        assert r.status_code == 200 and r.text == "console.log(1)"
        r = await client.get("/favicon.svg")
        assert r.text == "<svg/>"
        r = await client.get("/assets/missing.js")
        assert r.status_code == 404


async def test_api_404_not_swallowed_by_spa(env: Env) -> None:
    (env.static_dir / "index.html").write_text("<html>shell</html>")
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        static_dir=env.static_dir,
        allowed_hosts=TEST_HOSTS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/nope", "/api", "/media/1/missing.jpg", "/media/x"):
            r = await client.get(path)
            assert r.status_code == 404, path
            assert "shell" not in r.text
        r = await client.get("/openapi.json")
        assert r.status_code == 200 and "openapi" in r.json()
        r = await client.get("/docs")
        assert r.status_code == 200 and "shell" not in r.text


async def test_unknown_api_route_non_get_is_404(env: Env) -> None:
    (env.static_dir / "index.html").write_text("<html>shell</html>")
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        static_dir=env.static_dir,
        allowed_hosts=TEST_HOSTS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/nope", "/api/searches/1/nope", "/media/x"):
                r = await client.request(method, path)
                assert r.status_code == 404, (method, path)
                assert r.json() == {"detail": "Not Found"}
        # A known API path with the wrong method is still a 405.
        r = await client.delete("/api/settings")
        assert r.status_code == 405


async def test_unstarted_event_stream_leaves_no_subscriber(env: Env) -> None:
    from adscout.api.routes import search_events

    r = await env.client.post("/api/searches", json={"keyword": KEYWORD, "country": "US"})
    sid = r.json()["snapshot_id"]
    await env.source.started.wait()
    run = env.manager._find(sid)
    assert run is not None
    # The response object is built but its body never iterated (client went away).
    response = await search_events(sid, env.app.state.api)
    assert response.status_code == 200
    assert run.subscribers == set()
    env.source.gate.set()
    await wait_idle(env.manager)


async def test_root_without_static_shows_build_hint(env: Env) -> None:
    r = await env.client.get("/")
    assert r.status_code == 404
    assert "npm --prefix web ci && npm --prefix web run build" in r.text


async def test_shutdown_is_noop_when_idle(env: Env) -> None:
    await env.manager.shutdown()
    assert isinstance(env.manager, SearchManager)


async def test_untrusted_host_rejected(env: Env) -> None:
    r = await env.client.get("/api/settings", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


async def test_cross_site_post_blocked(env: Env) -> None:
    r = await env.client.post(
        "/api/searches/1/cancel", headers={"Origin": "https://evil.example.com"}
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "Cross-site request blocked."}


@pytest.mark.parametrize(
    "origin", ["http://localhost:5173", "http://127.0.0.1:8787", "http://localhost:8787"]
)
async def test_local_origin_post_allowed(env: Env, origin: str) -> None:
    r = await env.client.post("/api/searches/1/cancel", headers={"Origin": origin})
    assert r.status_code == 200 and r.json() == {"cancelled": False}


@pytest.mark.parametrize(
    "origin", ["http://localhost.evil.com", "http://127.0.0.1.evil.com:80", "null"]
)
async def test_lookalike_origin_post_blocked(env: Env, origin: str) -> None:
    r = await env.client.post("/api/searches/1/cancel", headers={"Origin": origin})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "origin", ["http://localhost", "http://127.0.0.1:3000", "http://localhost:8788"]
)
async def test_local_origin_on_another_port_blocked(env: Env, origin: str) -> None:
    # Only the served port (8787 here) and the Vite dev port may spend quota.
    r = await env.client.post("/api/searches/1/cancel", headers={"Origin": origin})
    assert r.status_code == 403


async def test_local_origin_follows_the_bound_port(env: Env) -> None:
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        port=9001,
        allowed_hosts=TEST_HOSTS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post(
            "/api/searches/1/cancel", headers={"Origin": "http://127.0.0.1:9001"}
        )
        other = await client.post(
            "/api/searches/1/cancel", headers={"Origin": "http://127.0.0.1:8787"}
        )
    assert ok.status_code == 200
    assert other.status_code == 403


async def test_portless_origin_is_port_80(env: Env) -> None:
    app = create_app(
        env.settings, engine=env.engine, manager=env.manager, port=80, allowed_hosts=TEST_HOSTS
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for origin in ("http://localhost", "http://127.0.0.1", "http://localhost:80"):
            r = await client.post("/api/searches/1/cancel", headers={"Origin": origin})
            assert r.status_code == 200, origin
        r = await client.post("/api/searches/1/cancel", headers={"Origin": "http://localhost:8787"})
        assert r.status_code == 403


async def test_cross_site_get_not_blocked_by_origin(env: Env) -> None:
    r = await env.client.get("/api/settings", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200


async def test_spa_accepts_head(env: Env) -> None:
    (env.static_dir / "index.html").write_text("<html>shell</html>")
    app = create_app(
        env.settings,
        engine=env.engine,
        manager=env.manager,
        static_dir=env.static_dir,
        allowed_hosts=TEST_HOSTS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/", "/report/12"):
            r = await client.head(path)
            assert r.status_code == 200, path


@pytest.mark.parametrize("host", ["localhost:5173", "127.0.0.1:8787", "localhost"])
async def test_loopback_hosts_allowed(env: Env, host: str) -> None:
    # The Vite dev proxy keeps the browser's Host (changeOrigin: false): localhost:5173.
    r = await env.client.get("/api/settings", headers={"Host": host})
    assert r.status_code == 200


BLOB = "0123456789abcdef0123456789abcdef.png"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (f"/media/blobs/{BLOB}", 200),
        ("/media/blobs/..%2f..%2fadscout.db", 404),
        ("/media/blobs/..%5cindex.json", 404),
        ("/media/blobs/%2e%2e", 404),
        ("/media/blobs/.hidden", 404),
        ("/media/blobs/a b.png", 404),
        ("/media/blobs/missing.png", 404),
        ("/media/blobs/sub", 404),
        ("/media/blobs/%2fetc%2fpasswd", 404),
    ],
)
async def test_blob_route_validates_filename(env: Env, path: str, code: int) -> None:
    blobs = env.settings.media_dir / "blobs"
    blobs.mkdir(parents=True)
    (blobs / BLOB).write_bytes(b"png")
    (blobs / ".hidden").write_bytes(b"x")
    (blobs / "sub").mkdir()
    (env.settings.media_dir / "index.json").write_text("{}")
    r = await env.client.get(path)
    assert r.status_code == code
    if code == 200:
        assert r.content == b"png"
        assert "immutable" in r.headers["cache-control"]


async def test_search_finishing_during_shutdown_starts_no_analysis(tmp_path: Path) -> None:
    """Searches stop before the analysis manager: a search that completes while the app
    shuts down must not start an analysis on a manager that has already stopped."""
    from adscout.sources import CancelToken, ProgressCallback, SearchResult

    order: list[str] = []
    release = asyncio.Event()
    started = asyncio.Event()

    class FinishingSource:
        name = "fake"

        def supports(self, q: SearchQuery) -> bool:
            return True

        async def search(
            self,
            q: SearchQuery,
            progress: ProgressCallback | None = None,
            cancel: CancelToken | None = None,
        ) -> SearchResult:
            started.set()
            assert cancel is not None
            done = asyncio.ensure_future(release.wait())
            stop = asyncio.ensure_future(cancel.wait())
            await asyncio.wait({done, stop}, return_when=asyncio.FIRST_COMPLETED)
            done.cancel()
            stop.cancel()
            return SearchResult(ads=[], status="ok")  # finishes either way

    class FakeAnalysis:
        async def start_after_search(self, snapshot_id: int) -> None:
            order.append("analysis started")

        async def shutdown(self) -> None:
            order.append("analysis shutdown")
            release.set()  # the search finishes while shutdown is in progress
            for _ in range(20):
                await asyncio.sleep(0)

    settings = Settings(_env_file=None, data_dir=tmp_path)
    engine = make_engine(settings.db_path)
    analysis = FakeAnalysis()
    source = FinishingSource()
    manager = SearchManager(engine, lambda q: source, None, analysis=analysis)
    app = create_app(
        settings,
        engine=engine,
        manager=manager,
        analysis=analysis,  # type: ignore[arg-type]
        static_dir=tmp_path / "none",
    )
    async with app.router.lifespan_context(app):
        await manager.start(SearchQuery(keyword="x"))
        await started.wait()
    for _ in range(20):
        await asyncio.sleep(0)
    assert order == ["analysis shutdown"]
