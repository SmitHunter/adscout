import json

import pytest

from adscout import __version__
from adscout.analysis.backends import UNAVAILABLE
from adscout.models import SearchQuery
from adscout.settings_store import settings_path
from adscout.sources.meta_web.source import MetaWebSource
from adscout.sources.select import source_factory
from adscout.watch import notify

from .conftest import Env


@pytest.fixture(autouse=True)
def _no_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    # Availability must not depend on whether this machine has Claude Code installed.
    monkeypatch.setattr("adscout.analysis.backends.shutil.which", lambda _name: None)


async def test_settings_include_analysis_availability(env: Env) -> None:
    r = await env.client.get("/api/settings")
    assert r.status_code == 200
    assert r.json() == {
        "timezone": "Australia/Melbourne",
        "max_results": 300,
        "has_meta_token": False,
        "version": __version__,
        "analysis": {"available": False, "backend": None, "reason": UNAVAILABLE},
        "analysis_backend": "auto",
        "analysis_model": "claude-sonnet-5-5",
        "auto_analyze": False,
        "scroll_delay_min": 2.0,
        "scroll_delay_max": 5.0,
        "daily_check_time": "09:00",
        "desktop_notifications": False,
        "notifications_available": notify.available(),
        "max_auto_analyses_per_day": 5,
        "demo": False,
    }


async def test_put_settings_validates_and_persists(env: Env) -> None:
    r = await env.client.put(
        "/api/settings",
        json={"auto_analyze": True, "analysis_model": "claude-opus-5-5", "max_results": 50},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["auto_analyze"] is True and body["analysis_model"] == "claude-opus-5-5"
    assert body["max_results"] == 50 and body["analysis_backend"] == "auto"
    saved = json.loads(settings_path(env.settings.data_dir).read_text(encoding="utf-8"))
    assert saved == {"analysis_model": "claude-opus-5-5", "auto_analyze": True, "max_results": 50}
    assert (await env.client.get("/api/settings")).json() == body

    for bad in (
        {"analysis_backend": "gpt"},
        {"analysis_model": "claude-2"},
        {"max_results": 0},
        {"scroll_delay_min": 0.1},
        {"scroll_delay_min": 9.0, "scroll_delay_max": 3.0},
        {"unknown_field": 1},
    ):
        r = await env.client.put("/api/settings", json=bad)
        assert r.status_code == 422, bad
    # Invalid writes leave the saved settings untouched.
    assert (await env.client.get("/api/settings")).json() == body


async def test_put_one_scroll_delay_checks_against_the_other(env: Env) -> None:
    r = await env.client.put("/api/settings", json={"scroll_delay_max": 10.0})
    assert r.status_code == 200
    r = await env.client.put("/api/settings", json={"scroll_delay_min": 8.0})
    assert r.status_code == 200
    assert (r.json()["scroll_delay_min"], r.json()["scroll_delay_max"]) == (8.0, 10.0)
    r = await env.client.put("/api/settings", json={"scroll_delay_min": 12.0})
    assert r.status_code == 422
    assert "Scroll delays" in r.json()["detail"]


async def test_put_settings_cross_origin_403(env: Env) -> None:
    r = await env.client.put(
        "/api/settings",
        json={"auto_analyze": True},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert not settings_path(env.settings.data_dir).exists()


async def test_settings_change_applies_to_next_search(env: Env) -> None:
    factory = source_factory(env.settings)
    r = await env.client.put(
        "/api/settings", json={"max_results": 42, "scroll_delay_min": 1.0, "scroll_delay_max": 2.5}
    )
    assert r.status_code == 200
    source = factory(SearchQuery(keyword="x"))
    assert isinstance(source, MetaWebSource)
    s = source._settings
    assert (s.max_results, s.scroll_delay_min, s.scroll_delay_max) == (42, 1.0, 2.5)


async def test_put_one_scroll_delay_saves_only_that_field(env: Env) -> None:
    # An .env min of 1.0 is below UserSettings' default min of 2.0.
    env.settings.scroll_delay_min = 1.0
    env.settings.scroll_delay_max = 4.0
    r = await env.client.put("/api/settings", json={"scroll_delay_max": 1.5})
    assert r.status_code == 200, r.json()
    assert (r.json()["scroll_delay_min"], r.json()["scroll_delay_max"]) == (1.0, 1.5)
    saved = json.loads(settings_path(env.settings.data_dir).read_text(encoding="utf-8"))
    assert saved == {"scroll_delay_max": 1.5}
    # The .env min is still the effective one.
    assert (await env.client.get("/api/settings")).json()["scroll_delay_min"] == 1.0
    r = await env.client.put("/api/settings", json={"scroll_delay_max": 0.8})
    assert r.status_code == 422


class _Scheduler:
    def __init__(self) -> None:
        self.rescheduled = 0

    def reschedule(self) -> None:
        self.rescheduled += 1

    async def shutdown(self) -> None:
        pass


async def test_put_watch_settings_validates_and_reschedules(env: Env) -> None:
    scheduler = _Scheduler()
    env.app.state.api.scheduler = scheduler
    r = await env.client.put(
        "/api/settings",
        json={
            "daily_check_time": "7:30",
            "desktop_notifications": True,
            "max_auto_analyses_per_day": 2,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["daily_check_time"] == "07:30"
    assert body["desktop_notifications"] is True
    assert body["max_auto_analyses_per_day"] == 2
    assert scheduler.rescheduled == 1

    for bad in ("25:00", "9", "09:60", "nine"):
        r = await env.client.put("/api/settings", json={"daily_check_time": bad})
        assert r.status_code == 422, bad
        assert r.json()["detail"] == "Use a 24-hour time like 09:00."
    r = await env.client.put("/api/settings", json={"max_auto_analyses_per_day": -1})
    assert r.status_code == 422
    assert scheduler.rescheduled == 1  # invalid writes never reschedule
    assert (await env.client.get("/api/settings")).json() == body
