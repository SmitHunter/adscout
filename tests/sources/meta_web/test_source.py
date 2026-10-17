import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from adscout.config import Settings
from adscout.models import ScrapeProgress, SearchQuery
from adscout.sources import CancelToken, SourceError
from adscout.sources.meta_web.driver import PlaywrightDriver
from adscout.sources.meta_web.parse_graphql import parse_payloads
from adscout.sources.meta_web.source import MetaWebSource

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "meta_web"
LIBRARY_URL = "https://www.facebook.com/ads/library/?q=x"
NORMAL_TEXT = "Ad Library\nLog in\nSearch ads"


def _read(path: str) -> str:
    return (FIXTURES / path).read_text(encoding="utf-8")


def _posture_scrolls() -> list[list[str]]:
    return [[_read(f"posture-corrector-US/graphql_{i}.json")] for i in range(1, 5)]


class FakeDriver:
    """Scripted PageDriver: page 1 on open, one payload batch per scroll."""

    def __init__(
        self,
        open_payloads: list[str],
        scroll_payloads: list[list[str]] | None = None,
        texts: list[str] | None = None,
        fail_on_scroll: int | None = None,
    ) -> None:
        self.open_payloads = open_payloads
        self.scroll_payloads = list(scroll_payloads or [])
        # texts[0] after open, texts[n] after scroll n; last value repeats.
        self.texts = texts or [NORMAL_TEXT]
        self.fail_on_scroll = fail_on_scroll
        self.pending: list[str] = []
        self.scrolls = 0
        self.opened_url: str | None = None
        self.closed = False
        self.settled = 0

    async def open(self, url: str) -> None:
        self.opened_url = url
        self.pending.extend(self.open_payloads)

    async def scroll(self) -> None:
        self.scrolls += 1
        if self.fail_on_scroll == self.scrolls:
            raise RuntimeError("browser crashed")
        if self.scrolls <= len(self.scroll_payloads):
            self.pending.extend(self.scroll_payloads[self.scrolls - 1])

    async def settle(self) -> None:
        self.settled += 1

    def drain_payloads(self) -> list[str]:
        out, self.pending = self.pending, []
        return out

    async def inner_text(self) -> str:
        return self.texts[min(self.scrolls, len(self.texts) - 1)]

    async def current_url(self) -> str:
        return LIBRARY_URL

    async def close(self) -> None:
        self.closed = True


class Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _source(
    driver: FakeDriver, sleeper: Sleeper | None = None, **settings: object
) -> MetaWebSource:
    factory: Callable[[], FakeDriver] = lambda: driver  # noqa: E731
    return MetaWebSource(
        Settings(**settings),  # type: ignore[arg-type]
        driver_factory=factory,
        sleep=sleeper or Sleeper(),
    )


def _posture_driver(**kwargs: object) -> FakeDriver:
    return FakeDriver([_read("posture-corrector-US/page.html")], _posture_scrolls(), **kwargs)  # type: ignore[arg-type]


async def test_collects_until_stall() -> None:
    driver = _posture_driver()
    sleeper = Sleeper()
    events: list[ScrapeProgress] = []

    result = await _source(driver, sleeper).search(
        SearchQuery(keyword="posture corrector"), progress=events.append
    )

    assert result.status == "ok"
    assert len(result.ads) == 69
    assert len({a.ad_archive_id for a in result.ads}) == 69
    # 4 productive scrolls, then 3 empty ones.
    assert driver.scrolls == 7
    assert len(sleeper.calls) == 7
    assert driver.closed
    assert driver.opened_url is not None and "posture+corrector" in driver.opened_url
    assert events[0].phase == "opening"
    assert events[-1].phase == "done"
    assert events[-1].ads_found == 69
    assert 0 < events[-1].advertisers <= 69
    assert any(e.phase == "scrolling" for e in events)


async def test_respects_limit() -> None:
    driver = _posture_driver()
    result = await _source(driver).search(SearchQuery(keyword="posture corrector", limit=5))
    assert result.status == "capped"
    assert len(result.ads) == 5
    assert driver.scrolls == 0
    assert driver.closed


async def test_settings_max_results_caps() -> None:
    driver = _posture_driver()
    result = await _source(driver, max_results=40).search(SearchQuery(keyword="posture corrector"))
    assert result.status == "capped"
    assert len(result.ads) == 40


async def test_cancel_midway() -> None:
    driver = _posture_driver()
    cancel = CancelToken()

    async def progress(p: ScrapeProgress) -> None:
        if p.phase == "scrolling" and p.ads_found > 30:
            cancel.cancel()

    result = await _source(driver).search(
        SearchQuery(keyword="posture corrector"), progress=progress, cancel=cancel
    )
    assert result.status == "cancelled"
    assert 30 < len(result.ads) < 69
    assert driver.scrolls == 1
    assert driver.closed


async def test_block_midway_keeps_ads() -> None:
    login_wall = _read("blocks/login_wall.txt")
    driver = _posture_driver(texts=[NORMAL_TEXT, NORMAL_TEXT, login_wall])
    result = await _source(driver).search(SearchQuery(keyword="posture corrector"))
    assert result.status == "blocked"
    assert result.message is not None
    assert "uv run adscout browser" in result.message
    assert len(result.ads) >= 30
    assert driver.scrolls == 2
    assert driver.closed


async def test_block_after_scroll_keeps_that_scrolls_ads() -> None:
    login_wall = _read("blocks/login_wall.txt")
    driver = _posture_driver(texts=[NORMAL_TEXT, NORMAL_TEXT, login_wall])
    result = await _source(driver).search(SearchQuery(keyword="posture corrector"))
    payloads = [
        _read("posture-corrector-US/page.html"),
        *_posture_scrolls()[0],
        *_posture_scrolls()[1],
    ]
    expected = {a.ad_archive_id for a in parse_payloads(payloads)}
    assert result.status == "blocked"
    assert {a.ad_archive_id for a in result.ads} == expected
    assert driver.pending == []


async def test_block_on_open() -> None:
    driver = FakeDriver([], texts=[_read("blocks/checkpoint.txt")])
    result = await _source(driver).search(SearchQuery(keyword="posture corrector"))
    assert result.status == "blocked"
    assert result.ads == []
    assert driver.scrolls == 0
    assert driver.closed


async def test_text_fallback_when_no_graphql() -> None:
    driver = FakeDriver([], texts=[_read("posture-corrector-US/inner_text.txt")])
    result = await _source(driver).search(SearchQuery(keyword="posture corrector"))
    assert result.status == "ok"
    assert len(result.ads) == 56
    assert driver.scrolls == 3


async def test_text_fallback_is_capped() -> None:
    driver = FakeDriver([], texts=[_read("posture-corrector-US/inner_text.txt")])
    result = await _source(driver).search(SearchQuery(keyword="posture corrector", limit=10))
    assert result.status == "capped"
    assert len(result.ads) == 10


async def test_zero_results_ok() -> None:
    driver = FakeDriver(
        [_read("zzqxwv-nothing-US/page.html")], texts=[_read("zzqxwv-nothing-US/inner_text.txt")]
    )
    result = await _source(driver).search(SearchQuery(keyword="zzqxwv nothing"))
    assert result.status == "ok"
    assert result.ads == []
    assert driver.scrolls == 3
    assert driver.closed


async def test_delays_within_bounds() -> None:
    driver = _posture_driver()
    sleeper = Sleeper()
    await _source(driver, sleeper).search(SearchQuery(keyword="posture corrector"))
    assert sleeper.calls
    assert all(2.0 <= s <= 5.0 for s in sleeper.calls)
    assert len(set(sleeper.calls)) > 1


async def test_driver_closed_on_error() -> None:
    driver = _posture_driver(fail_on_scroll=2)
    with pytest.raises(SourceError) as info:
        await _source(driver).search(SearchQuery(keyword="posture corrector"))
    assert isinstance(info.value.__cause__, RuntimeError)
    assert driver.closed


async def test_sync_progress_callback_supported() -> None:
    events: list[str] = []
    driver = FakeDriver([_read("zzqxwv-nothing-US/page.html")])
    await _source(driver).search(
        SearchQuery(keyword="x"), progress=lambda p: events.append(p.phase)
    )
    assert events[0] == "opening"
    assert events[-1] == "done"


def test_supports() -> None:
    src = MetaWebSource(Settings())
    assert src.name == "meta_web"
    assert src.supports(SearchQuery(keyword="x", country="AU"))
    assert src.supports(SearchQuery(keyword="x", country="us"))
    assert not src.supports(SearchQuery(keyword="x", country="EU"))


async def test_playwright_driver_close_cancels_pending_reads(tmp_path: Path) -> None:
    driver = PlaywrightDriver(tmp_path, headless=True)
    pending = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    driver._reads.add(pending)
    await driver.close()
    assert pending.cancelled()
    assert not driver._reads


class HangingOpenDriver(FakeDriver):
    """open() never returns, like a slow page.goto."""

    def __init__(self) -> None:
        super().__init__([])
        self.open_cancelled = False

    async def open(self, url: str) -> None:
        self.opened_url = url
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.open_cancelled = True
            raise


async def test_cancel_during_open_closes_driver() -> None:
    driver = HangingOpenDriver()
    cancel = CancelToken()
    task = asyncio.ensure_future(
        _source(driver).search(SearchQuery(keyword="posture corrector"), cancel=cancel)
    )
    await asyncio.sleep(0.01)
    cancel.cancel()
    result = await asyncio.wait_for(task, 1)
    assert result.status == "cancelled"
    assert result.ads == []
    assert driver.open_cancelled
    assert driver.closed


async def test_default_sleep_wakes_on_cancel() -> None:
    driver = _posture_driver()
    cancel = CancelToken()
    source = MetaWebSource(
        Settings(scroll_delay_min=30, scroll_delay_max=30),
        driver_factory=lambda: driver,
    )
    task = asyncio.ensure_future(
        source.search(SearchQuery(keyword="posture corrector"), cancel=cancel)
    )
    await asyncio.sleep(0.05)
    started = asyncio.get_running_loop().time()
    cancel.cancel()
    result = await asyncio.wait_for(task, 1)
    assert asyncio.get_running_loop().time() - started < 1
    assert result.status == "cancelled"
    assert result.ads  # page 1 kept
    assert driver.scrolls == 0
    assert driver.closed


async def test_cancel_token_sleep_and_threadsafe_cancel() -> None:
    import threading

    cancel = CancelToken()
    assert await cancel.sleep(0.01) is False
    loop = asyncio.get_running_loop()
    timer = threading.Timer(0.05, cancel.cancel)
    timer.start()
    started = loop.time()
    assert await cancel.sleep(30) is True
    assert loop.time() - started < 1
    assert cancel.cancelled
    # Already cancelled: returns at once.
    assert await cancel.sleep(30) is True
    await asyncio.wait_for(cancel.wait(), 0.1)
