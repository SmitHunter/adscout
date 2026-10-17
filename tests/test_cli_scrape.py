import asyncio
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session
from typer.testing import CliRunner

import adscout.cli as cli
from adscout.cli import app
from adscout.config import Settings
from adscout.db.repo import list_snapshots, load_ads, make_engine
from adscout.models import Ad, ScrapeProgress, SearchQuery
from adscout.sources import CancelToken, ProgressCallback, SearchResult, SourceError
from adscout.sources.select import get_source


def _ad(ad_id: str, page_name: str, day: int = 10) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=f"pid-{page_name}",
        page_name=page_name,
        start_date=datetime(2026, 3, day, 20, 0, tzinfo=UTC),
        is_active=True,
    )


ADS = [
    _ad("1", "Alpha Store", day=2),
    _ad("2", "Alpha Store"),
    _ad("3", "Alpha Store"),
    _ad("4", "Beta Shop"),
    _ad("5", "Beta Shop"),
    _ad("6", "Gamma Co"),
]


class FakeSource:
    name = "fake"

    def __init__(self, result: SearchResult | None = None, *, wait_for_cancel: bool = False):
        self.result = result or SearchResult(ads=ADS, status="ok")
        self.wait_for_cancel = wait_for_cancel
        self.queries: list[SearchQuery] = []

    def supports(self, q: SearchQuery) -> bool:
        return q.country != "EU"

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        self.queries.append(q)
        if progress is not None:
            progress(ScrapeProgress(ads_found=2, advertisers=1, phase="scrolling", message="x"))
        if self.wait_for_cancel:
            assert cancel is not None
            signal.raise_signal(signal.SIGINT)  # simulated Ctrl+C
            for _ in range(100):
                if cancel.cancelled:
                    return SearchResult(ads=ADS[:2], status="cancelled", message="Cancelled.")
                await asyncio.sleep(0)
            raise AssertionError("Ctrl+C did not cancel the search")
        return self.result


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Never read the developer's .env or data/settings.json.
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(None, tmp_path))


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = make_engine(tmp_path / "adscout.db")
    monkeypatch.setattr(cli, "get_engine", lambda: eng)
    return eng


def _use(monkeypatch: pytest.MonkeyPatch, source: FakeSource) -> list[Settings]:
    seen: list[Settings] = []

    def factory(q: SearchQuery, settings: Settings, choice: str = "auto") -> FakeSource:
        seen.append(settings)
        return source

    monkeypatch.setattr(cli, "get_source", factory)
    return seen


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "160"})
    return result.exit_code, result.output


def test_scrape_saves_snapshot_and_prints_summary(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    _use(monkeypatch, source)

    code, out = _invoke("scrape", "posture corrector", "--country", "au", "--limit", "50")

    assert code == 0, out
    [q] = source.queries
    assert (q.keyword, q.country, q.limit, q.active_status) == (
        "posture corrector",
        "AU",
        50,
        "active",
    )
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert snap.status == "ok"
        assert snap.ad_count == 6
        assert snap.advertiser_count == 3
        assert len(load_ads(s, snap.id or 0)) == 6
    assert f"Snapshot #{snap.id}" in out
    assert "Alpha Store" in out and "Beta Shop" in out and "Gamma Co" in out
    # Oldest ad 2026-03-02 20:00 UTC is 2026-03-03 07:00 in Melbourne.
    assert "2026-03-03" in out
    assert "days" in out


def test_scrape_flags_build_query(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeSource()
    seen = _use(monkeypatch, source)

    code, out = _invoke("scrape", "lamp", "--all", "--media", "video", "--headless")

    assert code == 0, out
    [q] = source.queries
    assert (q.country, q.active_status, q.media_type, q.limit) == ("US", "all", "video", 300)
    assert seen[0].headless is True


def test_scrape_zero_results_message(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeSource(SearchResult(ads=[], status="ok")))

    code, out = _invoke("scrape", "zzqxwv")

    assert code == 0, out
    assert 'No ads found for "zzqxwv" in US. Try a broader keyword.' in out
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert snap.status == "ok" and snap.ad_count == 0


def test_scrape_blocked_exit_code_2(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    msg = "Login required. Run: uv run adscout browser — sign in once, then retry."
    _use(monkeypatch, FakeSource(SearchResult(ads=ADS[:2], status="blocked", message=msg)))

    code, out = _invoke("scrape", "posture corrector")

    assert code == 2
    assert "uv run adscout browser" in out
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert snap.status == "blocked" and snap.ad_count == 2


def test_scrape_source_error_exit_1(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FakeSource):
        async def search(self, *args: object, **kwargs: object) -> SearchResult:
            raise SourceError("browser crashed")

    _use(monkeypatch, Broken())

    code, out = _invoke("scrape", "posture corrector")

    assert code == 1
    assert "browser crashed" in out
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert snap.status == "error"


def test_scrape_profile_busy_exit_1(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from adscout.locks import PROFILE_BUSY_MESSAGE, ProfileBusy

    class Busy(FakeSource):
        async def search(self, *args: object, **kwargs: object) -> SearchResult:
            raise ProfileBusy()

    _use(monkeypatch, Busy())

    code, out = _invoke("scrape", "posture corrector")

    assert code == 1
    assert PROFILE_BUSY_MESSAGE == (
        "Another AdScout search is using the browser. Wait for it to finish, then try again."
    )
    assert PROFILE_BUSY_MESSAGE in " ".join(out.split())
    assert "Scrape failed" not in out


def test_scrape_rejects_bad_country(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape", "lamp", "--country", "USA")
    assert code == 1
    assert "two letters" in out


def test_scrape_unsupported_country(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape", "lamp", "--country", "EU")
    assert code == 1
    assert "EU" in out
    with Session(engine) as s:
        assert list_snapshots(s) == []


def test_scrape_ctrl_c_cancels_gracefully(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeSource(wait_for_cancel=True))
    before = signal.getsignal(signal.SIGINT)

    code, out = _invoke("scrape", "posture corrector")

    assert code == 0, out
    assert signal.getsignal(signal.SIGINT) is before
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert snap.status == "cancelled" and snap.ad_count == 2


def test_snapshots_lists_runs(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeSource())
    assert _invoke("scrape", "posture corrector")[0] == 0
    assert _invoke("scrape", "neck massager", "--country", "AU")[0] == 0

    code, out = _invoke("snapshots")
    assert code == 0, out
    assert "posture corrector" in out and "neck massager" in out
    assert out.index("neck massager") < out.index("posture corrector")  # newest first
    assert "AU" in out and "ok" in out

    code, out = _invoke("snapshots", "neck massager")
    assert code == 0, out
    assert "neck massager" in out and "posture corrector" not in out


def test_snapshots_empty(engine: Engine) -> None:
    code, out = _invoke("snapshots")
    assert code == 0
    assert "No snapshots yet" in out


def test_get_source_returns_meta_web(tmp_path: Path) -> None:
    from adscout.sources.meta_web.source import MetaWebSource

    src = get_source(SearchQuery(keyword="x"), _settings(None, tmp_path))
    assert isinstance(src, MetaWebSource)


def _settings(token: str | None, data_dir: Path, **extra: Any) -> Settings:
    from pydantic import SecretStr

    return Settings(
        _env_file=None,
        data_dir=data_dir,
        meta_access_token=SecretStr(token) if token else None,
        **extra,
    )


@pytest.mark.parametrize(
    ("country", "token", "choice", "expected"),
    [
        ("DE", "t", "auto", "meta_api"),
        ("GB", "t", "auto", "meta_api"),
        ("US", "t", "auto", "meta_web"),
        ("DE", None, "auto", "meta_web"),
        ("DE", "t", "web", "meta_web"),
        ("US", "t", "api", "meta_api"),
    ],
)
def test_get_source_selection(
    country: str, token: str | None, choice: str, expected: str, tmp_path: Path
) -> None:
    q = SearchQuery(keyword="x", country=country)
    assert get_source(q, _settings(token, tmp_path), choice).name == expected


def test_source_api_without_token_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(None, tmp_path))
    code, out = _invoke("scrape", "x", "--country", "DE", "--source", "api")
    assert code == 1
    assert "META_ACCESS_TOKEN is not set. Add it to .env (see .env.example)." in out


MARKUP_KW = "lamp [/x] [red]"


def test_scrape_and_snapshots_escape_markup(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ads = [_ad("1", "[bold]Shop[/] [/x]"), _ad("2", "Plain")]
    _use(monkeypatch, FakeSource(SearchResult(ads=ads, status="ok")))

    code, out = _invoke("scrape", MARKUP_KW)
    assert code == 0, out
    assert MARKUP_KW in out
    assert "[bold]Shop[/] [/x]" in out

    code, out = _invoke("snapshots")
    assert code == 0, out
    assert MARKUP_KW in out

    code, out = _invoke("snapshots", MARKUP_KW)
    assert code == 0, out
    assert MARKUP_KW in out


def test_scrape_zero_results_escapes_markup(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, FakeSource(SearchResult(ads=[], status="ok")))
    code, out = _invoke("scrape", MARKUP_KW)
    assert code == 0, out
    assert f'No ads found for "{MARKUP_KW}"' in out


def test_scrape_error_and_blocked_messages_escape_markup(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken(FakeSource):
        async def search(self, *args: object, **kwargs: object) -> SearchResult:
            raise SourceError("bad [/x] thing")

    _use(monkeypatch, Broken())
    code, out = _invoke("scrape", MARKUP_KW)
    assert code == 1
    assert "bad [/x] thing" in out

    msg = "Blocked [/x] [red]"
    _use(monkeypatch, FakeSource(SearchResult(ads=[], status="blocked", message=msg)))
    code, out = _invoke("scrape", MARKUP_KW)
    assert code == 2
    assert msg in out

    msg = "Capped [/x] at 1"
    _use(monkeypatch, FakeSource(SearchResult(ads=ADS[:1], status="capped", message=msg)))
    code, out = _invoke("scrape", MARKUP_KW)
    assert code == 0, out
    assert msg in out


def test_scrape_eu_without_token_says_set_token(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(None, tmp_path))
    _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape", "lamp", "--country", "EU")
    assert code == 1
    assert "META_ACCESS_TOKEN" in out
    assert "official" in out


@pytest.mark.parametrize(
    ("configured", "flag", "expected"),
    [
        (True, "--no-headless", False),
        (False, "--headless", True),
        (True, None, True),
        (False, None, False),
    ],
)
def test_headless_flag_overrides_settings(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: bool,
    flag: str | None,
    expected: bool,
) -> None:
    settings = _settings(None, tmp_path, headless=configured)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    seen = _use(monkeypatch, FakeSource())
    args = ["scrape", "lamp"] + ([flag] if flag else [])
    code, out = _invoke(*args)
    assert code == 0, out
    assert seen[0].headless is expected


def test_scrape_and_snapshots_survive_cp1252_emoji_and_cjk(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ads = [_ad("1", "Shop :fire:"), _ad("2", "姿勢 Store 🔥", day=2)]
    _use(monkeypatch, FakeSource(SearchResult(ads=ads, status="ok")))
    runner = CliRunner(charset="cp1252")

    result = runner.invoke(app, ["scrape", "posture 🔥 :fire:"], env={"COLUMNS": "160"})
    assert result.exception is None, result.output
    assert result.exit_code == 0
    assert "Shop :fire:" in result.output
    assert "?? Store ?" in result.output

    result = runner.invoke(app, ["snapshots"], env={"COLUMNS": "160"})
    assert result.exception is None, result.output
    assert result.exit_code == 0
    assert "posture ? :fire:" in result.output


def test_scrape_uses_saved_user_settings(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adscout.settings_store import update_user_settings

    monkeypatch.setattr(cli, "get_settings", lambda: _settings(None, tmp_path))
    update_user_settings(tmp_path, {"max_results": 7, "scroll_delay_max": 9.0})
    seen = _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape", "lamp")
    assert code == 0, out
    assert (seen[0].max_results, seen[0].scroll_delay_max) == (7, 9.0)


def test_scrape_advertiser_page(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeSource()
    _use(monkeypatch, source)

    code, out = _invoke("scrape", "--advertiser", "1234567", "--name", "Alpha Store")

    assert code == 0, out
    [q] = source.queries
    assert (q.kind, q.page_id, q.keyword, q.country) == (
        "advertiser",
        "1234567",
        "Alpha Store",
        "ALL",
    )
    with Session(engine) as s:
        [snap] = list_snapshots(s)
        assert (snap.kind, snap.page_id, snap.query) == ("advertiser", "1234567", "Alpha Store")


def test_scrape_advertiser_country_and_default_name(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    _use(monkeypatch, source)

    code, out = _invoke("scrape", "--advertiser", "42", "--country", "gb")

    assert code == 0, out
    [q] = source.queries
    assert (q.kind, q.page_id, q.keyword, q.country) == ("advertiser", "42", "Page 42", "GB")


def test_scrape_needs_keyword_or_advertiser(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape")
    assert code == 1
    assert "Give a keyword, or --advertiser PAGE_ID." in out


def test_scrape_advertiser_rejects_bad_page_id(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape", "--advertiser", "abc", "--name", "X")
    assert code == 1
    assert "page_id" in out


def test_scrape_advertiser_zero_results_message(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, FakeSource(SearchResult(ads=[], status="ok")))
    code, out = _invoke("scrape", "--advertiser", "42", "--name", "Alpha")
    assert code == 0, out
    assert 'No ads found for advertiser "Alpha" (page 42) in ALL.' in out


def test_scrape_name_needs_advertiser(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeSource())
    code, out = _invoke("scrape", "lamp", "--name", "X")
    assert code == 1
    assert "--name is only used with --advertiser PAGE_ID." in out
