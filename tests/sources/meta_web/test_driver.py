"""PlaywrightDriver lifecycle without a real browser (Playwright is faked)."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from adscout.config import Settings
from adscout.db.repo import list_snapshots, make_engine
from adscout.models import SearchQuery
from adscout.services.scrape import run_scrape
from adscout.sources import SourceError
from adscout.sources.meta_web import driver as driver_module
from adscout.sources.meta_web.driver import PlaywrightDriver, launch_context
from adscout.sources.meta_web.source import MetaWebSource


class FakeLock:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


class FakePage:
    url = "https://www.facebook.com/ads/library/"

    def on(self, event: str, handler: Any) -> None:
        pass

    async def goto(self, url: str, **kwargs: Any) -> None:
        pass

    async def content(self) -> str:
        return "<html></html>"


class FakeContext:
    def __init__(self) -> None:
        self.pages = [FakePage()]
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False

    async def start(self) -> "FakePlaywright":
        return self

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "lock": FakeLock(),
        "pw": FakePlaywright(),
        "context": FakeContext(),
        "launching": asyncio.Event(),
        "gate": asyncio.Event(),
    }

    async def lock_profile(profile_dir: Path) -> FakeLock:
        lock: FakeLock = state["lock"]
        return lock

    async def launch_context(pw: Any, profile_dir: Path, headless: bool) -> FakeContext:
        state["launching"].set()
        # Like a browser launch that doesn't stop when its task is cancelled.
        while True:
            try:
                await state["gate"].wait()
                break
            except asyncio.CancelledError:
                continue
        context: FakeContext = state["context"]
        return context

    monkeypatch.setattr(driver_module, "lock_profile", lock_profile)
    monkeypatch.setattr(driver_module, "async_playwright", lambda: state["pw"])
    monkeypatch.setattr(driver_module, "launch_context", launch_context)
    return state


async def test_open_completes_normally(fakes: dict[str, Any], tmp_path: Path) -> None:
    fakes["gate"].set()
    drv = PlaywrightDriver(tmp_path, headless=True)
    await drv.open("https://www.facebook.com/ads/library/")
    assert drv.drain_payloads() == ["<html></html>"]
    await drv.close()
    assert fakes["context"].closed and fakes["pw"].stopped
    assert fakes["lock"].released == 1


async def test_close_during_open_tears_down_what_open_starts_later(
    fakes: dict[str, Any], tmp_path: Path
) -> None:
    drv = PlaywrightDriver(tmp_path, headless=True)
    opening = asyncio.create_task(drv.open("https://www.facebook.com/ads/library/"))
    await fakes["launching"].wait()
    opening.cancel()  # what a cancelled search does; the launch carries on
    await drv.close()
    assert fakes["lock"].released == 1 and fakes["pw"].stopped
    fakes["gate"].set()  # the browser launch finishes after close()
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await opening
    # The late browser is closed, not left running with the profile unlocked.
    assert fakes["context"].closed
    assert drv._context is None and drv._page is None
    assert fakes["lock"].released == 1


FRIENDLY = "Chromium isn't installed. Run: uv run playwright install chromium"


class MissingChromium:
    async def launch_persistent_context(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "BrowserType.launch_persistent_context: Executable doesn't exist at /x/chrome\n"
            "Please run the following command to download new browsers:\n playwright install"
        )


class MissingChromiumPlaywright:
    chromium = MissingChromium()

    async def start(self) -> "MissingChromiumPlaywright":
        return self

    async def stop(self) -> None:
        pass


async def test_launch_context_explains_a_missing_chromium(tmp_path: Path) -> None:
    with pytest.raises(SourceError) as info:
        await launch_context(MissingChromiumPlaywright(), tmp_path, headless=True)  # type: ignore[arg-type]
    assert str(info.value) == FRIENDLY


async def test_missing_chromium_hint_is_what_a_failed_search_shows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def lock_profile(profile_dir: Path) -> FakeLock:
        return FakeLock()

    monkeypatch.setattr(driver_module, "lock_profile", lock_profile)
    monkeypatch.setattr(driver_module, "async_playwright", MissingChromiumPlaywright)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    engine = make_engine(tmp_path / "t.db")
    with pytest.raises(SourceError) as info:
        await run_scrape(SearchQuery(keyword="x"), MetaWebSource(settings), engine)
    assert str(info.value) == FRIENDLY
    with Session(engine) as session:
        (snap,) = list_snapshots(session)
    assert snap.status == "error" and snap.error_message == FRIENDLY
