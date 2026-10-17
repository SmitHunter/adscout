"""One place decides where the browser-profile lock lives: ``profile_lock_dir``.

A settings object whose profile is outside the data dir proves every user of the lock
(routes, driver, startup sweep, watch runner) goes through it.
"""

import random
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session

from adscout import locks
from adscout.api.app import create_app
from adscout.config import Settings
from adscout.db.repo import create_snapshot, create_watch_item, make_engine
from adscout.db.tables import Snapshot
from adscout.locks import ProfileBusy, acquire_profile, profile_lock, profile_lock_dir
from adscout.models import SearchQuery
from adscout.services.searches import SearchManager
from adscout.sources.meta_web.driver import lock_profile
from adscout.watch.runner import WatchRunner

from .conftest import TEST_HOSTS


class ElsewhereSettings(Settings):
    """The profile lives in ``<data_dir>/../elsewhere/browser-profile``."""

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir.parent / "elsewhere" / "browser-profile"


def test_profile_lock_dir_is_the_profiles_parent(tmp_path: Path) -> None:
    assert profile_lock_dir(tmp_path / "data" / "browser-profile") == tmp_path / "data"


async def test_every_user_of_the_lock_uses_the_profile_lock_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks, "PROFILE_TIMEOUT_S", 0.1)
    settings = ElsewhereSettings(_env_file=None, data_dir=tmp_path / "data")
    lock_dir = tmp_path / "elsewhere"
    assert profile_lock_dir(settings.browser_profile_dir) == lock_dir
    engine = make_engine(settings.db_path)
    with Session(engine) as s:
        running = create_snapshot(s, SearchQuery(keyword="x"), "fake").id
        create_watch_item(s, SearchQuery(keyword="posture corrector"))

    held = profile_lock(lock_dir, timeout_s=0)
    acquire_profile(held)
    try:
        # Driver: the scraper's browser can't take the profile.
        with pytest.raises(ProfileBusy):
            await lock_profile(settings.browser_profile_dir)
        # Watch runner: the daily run sees the profile busy.
        manager = SearchManager(engine, lambda q: None, None)  # type: ignore[arg-type,return-value]
        runner = WatchRunner(engine, manager, settings=lambda: settings, rng=random.Random(1))
        summary = await runner.run_all(datetime(2026, 10, 15, tzinfo=UTC))
        assert summary.skipped == "profile_busy"
        # Startup sweep leaves the "running" row alone; the browser route answers 409.
        app = create_app(
            settings,
            engine=engine,
            static_dir=tmp_path / "none",
            open_browser=lambda: None,
            allowed_hosts=TEST_HOSTS,
        )
        async with app.router.lifespan_context(app):
            with Session(engine) as s:
                row = s.get(Snapshot, running)
                assert row is not None and row.status == "running"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post("/api/browser/open")
                assert r.status_code == 409
    finally:
        held.release()
    engine.dispose()
