"""``create_app(..., demo=True)``: live actions are refused, the scheduler never starts."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import Session

from adscout.api.app import create_app
from adscout.api.deps import ApiState
from adscout.config import Settings
from adscout.db.repo import create_watch_item, make_engine
from adscout.demo import DEMO_COPY
from adscout.models import SearchQuery

from .conftest import TEST_HOSTS, FakeWatchRunner, seed


class Spy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, state: ApiState) -> object:
        self.calls += 1
        raise AssertionError("the demo must never build the scheduler")


def _app(tmp_path: Path, *, demo: bool, scheduler: Spy | None = None) -> FastAPI:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data-demo")
    engine = make_engine(settings.db_path)
    seed(engine)
    with Session(engine) as s:
        create_watch_item(s, SearchQuery(keyword="posture corrector", country="US"))
    opened: list[str] = []
    return create_app(
        settings,
        engine=engine,
        open_browser=lambda: opened.append("opened"),
        watch_runner=FakeWatchRunner(),
        watch_scheduler=scheduler,  # type: ignore[arg-type]
        allowed_hosts=TEST_HOSTS,
        demo=demo,
    )


@pytest.fixture
async def demo_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = _app(tmp_path, demo=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_demo_blocks_live_actions(demo_client: httpx.AsyncClient) -> None:
    calls = [
        ("/api/searches", {"keyword": "posture brace", "country": "US"}),
        ("/api/watch/1/run", None),
        ("/api/browser/open", None),
        ("/api/snapshots/1/analysis", {"force": False}),
    ]
    for path, body in calls:
        r = await demo_client.post(path, json=body)
        assert r.status_code == 403, path
        assert r.json() == {"detail": DEMO_COPY}, path


async def test_demo_settings_and_reads(demo_client: httpx.AsyncClient) -> None:
    settings = (await demo_client.get("/api/settings")).json()
    assert settings["demo"] is True
    assert (await demo_client.get("/api/snapshots/1/report")).status_code == 200
    assert (await demo_client.get("/api/watch")).status_code == 200
    # Local-only changes still work: settings, overrides.
    r = await demo_client.put("/api/settings", json={"daily_check_time": "08:30"})
    assert r.status_code == 200 and r.json()["daily_check_time"] == "08:30"


async def test_demo_watchlist_shows_next_daily_slot(demo_client: httpx.AsyncClient) -> None:
    """No scheduler runs, yet enabled items show the next daily slot (not "paused")."""
    r = await demo_client.put("/api/settings", json={"daily_check_time": "08:30"})
    assert r.status_code == 200
    tz = ZoneInfo(r.json()["timezone"])
    items = (await demo_client.get("/api/watch")).json()
    assert items and all(item["enabled"] for item in items)
    for item in items:
        when = datetime.fromisoformat(item["next_run_at"]).astimezone(tz)
        assert (when.hour, when.minute) == (8, 30)
        assert timedelta(0) < when - datetime.now(UTC) <= timedelta(days=1)


async def test_real_app_reports_demo_false(tmp_path: Path) -> None:
    app = _app(tmp_path, demo=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/settings")).json()["demo"] is False


async def test_demo_no_scheduler(tmp_path: Path) -> None:
    spy = Spy()
    app = _app(tmp_path, demo=True, scheduler=spy)
    async with app.router.lifespan_context(app):
        state: ApiState = app.state.api
        assert state.scheduler is None
        assert state.demo is True
    assert spy.calls == 0
