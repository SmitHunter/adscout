"""Watchlist and alert endpoints: CRUD, run now, trend series, alerts read."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import add_alert, create_snapshot, create_watch_item, finish_snapshot
from adscout.db.tables import WatchItem
from adscout.locks import acquire_profile, profile_lock, profile_lock_dir
from adscout.models import SearchQuery
from adscout.sources import SearchResult
from adscout.watch.runner import ItemRun

from .conftest import ADS, KEYWORD, Env, wait_idle

EVIL = {"Origin": "https://evil.example.com"}
NEXT_RUN = datetime(2026, 10, 15, 22, 0, tzinfo=UTC)


def watch(engine: Engine, keyword: str = KEYWORD) -> int:
    with Session(engine) as s:
        item = create_watch_item(s, SearchQuery(keyword=keyword, country="US"))
        assert item.id is not None
        return item.id


def seed_watch(engine: Engine, item_id: int, status: str = "ok", ads: Any = None) -> int:
    with Session(engine) as s:
        item = s.get(WatchItem, item_id)
        assert item is not None
        q = SearchQuery(keyword=item.query, country=item.country)
        snap = create_snapshot(s, q, "fake", watch_item_id=item_id)
        assert snap.id is not None
        result = SearchResult.model_validate({"ads": ADS if ads is None else ads, "status": status})
        finish_snapshot(s, snap.id, result)
        return snap.id


class _Scheduler:
    def next_run_time(self) -> datetime:
        return NEXT_RUN

    def reschedule(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


async def _drain(env: Env) -> None:
    for _ in range(100):
        if not env.app.state.api.watch_tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("run-now task did not finish")


async def test_empty_watchlist(env: Env) -> None:
    r = await env.client.get("/api/watch")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_keyword_and_duplicate_409(env: Env) -> None:
    r = await env.client.post("/api/watch", json={"keyword": KEYWORD, "country": "US"})
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "keyword" and body["query"] == KEYWORD
    assert body["country"] == "US" and body["enabled"] is True
    assert body["latest"] is None and body["latest_point"] is None
    assert body["sparkline"] == [] and body["unread_alerts"] == 0
    assert body["next_run_at"] is None and body["running"] is False

    r = await env.client.post("/api/watch", json={"keyword": KEYWORD, "country": "US"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Already watching this."}
    # A different filter is a different watch item.
    r = await env.client.post("/api/watch", json={"keyword": KEYWORD, "country": "GB"})
    assert r.status_code == 201
    assert len((await env.client.get("/api/watch")).json()) == 2


async def test_create_advertiser(env: Env) -> None:
    r = await env.client.post(
        "/api/watch", json={"kind": "advertiser", "keyword": "Alpha Store", "page_id": "123"}
    )
    assert r.status_code == 201
    body = r.json()
    assert (body["kind"], body["query"], body["page_id"], body["country"]) == (
        "advertiser",
        "Alpha Store",
        "123",
        "ALL",
    )
    r = await env.client.post(
        "/api/watch", json={"kind": "advertiser", "keyword": "Renamed", "page_id": "123"}
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"keyword": ""},
        {"keyword": "x", "country": "ZZ"},
        {"kind": "advertiser", "keyword": "x"},
        {"kind": "advertiser", "keyword": "x", "page_id": "abc"},
    ],
)
async def test_create_invalid_422(env: Env, body: dict[str, Any]) -> None:
    r = await env.client.post("/api/watch", json=body)
    assert r.status_code == 422


async def test_patch_and_delete(env: Env) -> None:
    item_id = watch(env.engine)
    r = await env.client.patch(f"/api/watch/{item_id}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await env.client.get("/api/watch")).json()[0]["enabled"] is False
    r = await env.client.patch("/api/watch/999", json={"enabled": True})
    assert r.status_code == 404
    assert r.json() == {"detail": "Watch item 999 not found."}
    r = await env.client.patch(f"/api/watch/{item_id}", json={})
    assert r.status_code == 422

    r = await env.client.delete(f"/api/watch/{item_id}")
    assert r.status_code == 204
    assert (await env.client.get("/api/watch")).json() == []
    r = await env.client.delete(f"/api/watch/{item_id}")
    assert r.status_code == 404


async def test_list_shows_latest_sparkline_alerts_and_next_run(env: Env) -> None:
    item_id = watch(env.engine)
    off_id = watch(env.engine, "yoga mat")
    await env.client.patch(f"/api/watch/{off_id}", json={"enabled": False})
    first = seed_watch(env.engine, item_id)
    second = seed_watch(env.engine, item_id, ads=ADS[:2] + ADS[3:])
    blocked = seed_watch(env.engine, item_id, status="blocked", ads=[])
    with Session(env.engine) as s:
        add_alert(s, watch_item_id=item_id, snapshot_id=second, kind="change", message="m")
    env.app.state.api.scheduler = _Scheduler()

    items = {i["id"]: i for i in (await env.client.get("/api/watch")).json()}
    item = items[item_id]
    assert item["latest"]["id"] == blocked and item["latest"]["status"] == "blocked"
    assert item["latest_point"]["snapshot_id"] == second
    assert item["sparkline"] == [3, 2]
    assert item["unread_alerts"] == 1
    assert item["next_run_at"] == "2026-10-15T22:00:00Z"
    assert items[off_id]["next_run_at"] is None
    assert first != second


async def test_sparkline_keeps_last_14(env: Env) -> None:
    item_id = watch(env.engine)
    for n in range(16):
        seed_watch(env.engine, item_id, ads=ADS[: 1 + n % 3])
    [item] = (await env.client.get("/api/watch")).json()
    assert len(item["sparkline"]) == 14
    assert item["sparkline"] == [1 + n % 3 for n in range(2, 16)]


async def test_trend_shape(env: Env) -> None:
    item_id = watch(env.engine)
    first = seed_watch(env.engine, item_id, ads=ADS[:2])
    seed_watch(env.engine, item_id, status="blocked", ads=[])
    second = seed_watch(env.engine, item_id, status="capped")

    r = await env.client.get(f"/api/watch/{item_id}/trend")
    assert r.status_code == 200
    body = r.json()
    assert body["item"]["id"] == item_id
    points = body["points"]
    assert [p["snapshot_id"] for p in points] == [first, second]
    assert set(points[0]) == {
        "snapshot_id",
        "date",
        "started_at",
        "active_ads",
        "capped",
        "new_creatives",
        "advertisers",
        "winner_score",
        "market_score",
    }
    assert (points[0]["active_ads"], points[0]["capped"], points[0]["advertisers"]) == (
        2,
        False,
        1,
    )
    assert (points[1]["active_ads"], points[1]["capped"], points[1]["advertisers"]) == (
        3,
        True,
        2,
    )
    assert points[0]["new_creatives"] == 2 and points[1]["new_creatives"] == 1

    r = await env.client.get("/api/watch/999/trend")
    assert r.status_code == 404


async def test_run_now_202_then_409_while_running(env: Env) -> None:
    item_id = watch(env.engine)
    env.runner.gate = asyncio.Event()
    r = await env.client.post(f"/api/watch/{item_id}/run")
    assert r.status_code == 202
    assert r.json() == {"message": f"Checking {KEYWORD} now."}
    await asyncio.wait_for(env.runner.started.wait(), 1)
    assert env.runner.calls == [item_id]
    assert (await env.client.get("/api/watch")).json()[0]["running"] is True

    r = await env.client.post(f"/api/watch/{item_id}/run")
    assert r.status_code == 409
    assert r.json() == {"detail": f"{KEYWORD} is already being checked."}

    env.runner.gate.set()
    await _drain(env)
    r = await env.client.post(f"/api/watch/{item_id}/run")
    assert r.status_code == 202
    await _drain(env)
    assert env.runner.calls == [item_id, item_id]


async def _queue_behind_manual_search(env: Env, item_id: int) -> int:
    """A manual search runs (gated); the item's scheduled search waits behind it."""
    r = await env.client.post("/api/searches", json={"keyword": "other"})
    assert r.status_code == 201
    await env.source.started.wait()
    return await env.manager.enqueue(
        SearchQuery(keyword=KEYWORD, country="US"), "scheduled", watch_item_id=item_id
    )


async def _release(env: Env) -> None:
    env.source.gate.set()
    await wait_idle(env.manager)


async def test_run_now_409_while_its_search_is_queued(env: Env) -> None:
    item_id = watch(env.engine)
    await _queue_behind_manual_search(env, item_id)
    r = await env.client.post(f"/api/watch/{item_id}/run")
    assert r.status_code == 409
    assert (await env.client.get("/api/watch")).json()[0]["running"] is True
    await _release(env)
    assert env.runner.calls == []


def _stale_row(env: Env, item_id: int, status: str) -> None:
    """A snapshot left "queued"/"running" by a process that is gone (not this server's)."""
    with Session(env.engine) as s:
        create_snapshot(
            s, SearchQuery(keyword=KEYWORD), "fake", status=status, watch_item_id=item_id
        )


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_stale_db_row_does_not_block_run_now_or_delete(env: Env, status: str) -> None:
    item_id = watch(env.engine)
    _stale_row(env, item_id, status)
    assert (await env.client.get("/api/watch")).json()[0]["running"] is False
    r = await env.client.post(f"/api/watch/{item_id}/run")
    assert r.status_code == 202
    await _drain(env)
    assert env.runner.calls == [item_id]
    assert (await env.client.delete(f"/api/watch/{item_id}")).status_code == 204


async def test_other_process_running_row_blocks_while_it_holds_the_profile(env: Env) -> None:
    item_id = watch(env.engine)
    _stale_row(env, item_id, "running")  # e.g. ``adscout watch run ID`` in a terminal
    held = profile_lock(profile_lock_dir(env.settings.browser_profile_dir), timeout_s=0)
    acquire_profile(held)
    try:
        assert (await env.client.get("/api/watch")).json()[0]["running"] is True
        assert (await env.client.post(f"/api/watch/{item_id}/run")).status_code == 409
        r = await env.client.delete(f"/api/watch/{item_id}")
        assert r.status_code == 409
    finally:
        held.release()
    assert env.runner.calls == []
    assert (await env.client.delete(f"/api/watch/{item_id}")).status_code == 204


async def test_run_now_unknown_404(env: Env) -> None:
    r = await env.client.post("/api/watch/999/run")
    assert r.status_code == 404
    assert env.runner.calls == []


async def test_run_now_failure_is_logged_not_raised(
    env: Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = watch(env.engine)

    async def boom(item_id: int) -> ItemRun:
        raise RuntimeError("search exploded")

    monkeypatch.setattr(env.runner, "run_one", boom)
    with caplog.at_level("ERROR"):
        r = await env.client.post(f"/api/watch/{item_id}/run")
        assert r.status_code == 202
        await _drain(env)
    assert "search exploded" in caplog.text


async def test_alerts_list_is_limited_server_side(env: Env) -> None:
    from adscout.api.watch_routes import ALERTS_DEFAULT_LIMIT

    item_id = watch(env.engine)
    with Session(env.engine) as s:
        ids = [
            add_alert(s, watch_item_id=item_id, snapshot_id=None, kind="blocked", message=f"{n}").id
            for n in range(ALERTS_DEFAULT_LIMIT + 5)
        ]
    newest_first = ids[::-1]
    body = (await env.client.get("/api/alerts")).json()
    assert [a["id"] for a in body] == newest_first[:ALERTS_DEFAULT_LIMIT]
    body = (await env.client.get("/api/alerts", params={"limit": 3})).json()
    assert [a["id"] for a in body] == newest_first[:3]
    for bad in (0, 1001):
        r = await env.client.get("/api/alerts", params={"limit": bad})
        assert r.status_code == 422, bad


async def test_alerts_list_and_read(env: Env) -> None:
    item_id = watch(env.engine)
    with Session(env.engine) as s:
        a1 = add_alert(
            s, watch_item_id=item_id, snapshot_id=None, kind="change", message="one", change_pct=40
        ).id
        a2 = add_alert(s, watch_item_id=item_id, snapshot_id=None, kind="blocked", message="two").id

    body = (await env.client.get("/api/alerts")).json()
    assert [a["id"] for a in body] == [a2, a1]
    assert body[1] | {"created_at": None} == {
        "id": a1,
        "watch_item_id": item_id,
        "snapshot_id": None,
        "query": KEYWORD,
        "kind": "change",
        "change_pct": 40.0,
        "message": "one",
        "created_at": None,
        "read": False,
    }

    r = await env.client.post("/api/alerts/read", json={"ids": [a1]})
    assert r.status_code == 200 and r.json() == {"marked": 1}
    unread = (await env.client.get("/api/alerts", params={"unread": "true"})).json()
    assert [a["id"] for a in unread] == [a2]
    r = await env.client.post("/api/alerts/read", json={"all": True})
    assert r.json() == {"marked": 1}
    assert (await env.client.get("/api/alerts?unread=true")).json() == []
    assert len((await env.client.get("/api/alerts")).json()) == 2

    for bad in ({}, {"all": False}, {"ids": [a1], "all": True}):
        r = await env.client.post("/api/alerts/read", json=bad)
        assert r.status_code == 422, bad


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/watch", {"keyword": KEYWORD}),
        ("PATCH", "/api/watch/{id}", {"enabled": False}),
        ("DELETE", "/api/watch/{id}", None),
        ("POST", "/api/watch/{id}/run", None),
        ("POST", "/api/alerts/read", {"all": True}),
    ],
)
async def test_cross_origin_403(env: Env, method: str, path: str, body: Any) -> None:
    item_id = watch(env.engine, "yoga mat")
    with Session(env.engine) as s:
        add_alert(s, watch_item_id=item_id, snapshot_id=None, kind="change", message="m")
    r = await env.client.request(method, path.format(id=item_id), json=body, headers=EVIL)
    assert r.status_code == 403
    [item] = (await env.client.get("/api/watch")).json()
    assert item["enabled"] is True and item["unread_alerts"] == 1
    assert env.runner.calls == []


async def test_delete_refused_while_checking(env: Env) -> None:
    item_id = watch(env.engine)
    env.runner.gate = asyncio.Event()
    assert (await env.client.post(f"/api/watch/{item_id}/run")).status_code == 202
    await asyncio.wait_for(env.runner.started.wait(), 1)
    r = await env.client.delete(f"/api/watch/{item_id}")
    assert r.status_code == 409
    assert r.json() == {"detail": "This item is being checked. Try again when it finishes."}
    env.runner.gate.set()
    await _drain(env)
    assert (await env.client.delete(f"/api/watch/{item_id}")).status_code == 204


async def test_delete_refused_while_its_search_is_queued(env: Env) -> None:
    item_id = watch(env.engine)
    await _queue_behind_manual_search(env, item_id)
    r = await env.client.delete(f"/api/watch/{item_id}")
    assert r.status_code == 409
    await _release(env)
