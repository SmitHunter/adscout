import asyncio
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from adscout.models import SearchQuery
from adscout.sources import CancelToken, SourceBlocked
from adscout.sources.meta_web.urls import build_library_url


@pytest.mark.parametrize("kw", ['"knee brace" & sleeve', "#1 posture corrector", "crème"])
def test_build_library_url_encodes_keyword(kw: str) -> None:
    url = build_library_url(SearchQuery(keyword=kw))
    qs = parse_qs(urlparse(url).query)
    assert qs["q"] == [kw]
    assert qs["country"] == ["US"]
    assert qs["search_type"] == ["keyword_unordered"]
    assert qs["active_status"] == ["active"]
    assert qs["media_type"] == ["all"]
    assert url.startswith("https://www.facebook.com/ads/library/?")


def test_cancel_token() -> None:
    t = CancelToken()
    assert not t.cancelled
    t.cancel()
    assert t.cancelled


def test_source_blocked_message() -> None:
    e = SourceBlocked("Login wall.", "Log in once.")
    assert str(e) == "Login wall. Log in once."
    assert e.reason == "Login wall."
    assert e.how_to_fix == "Log in once."


async def test_unnamed_ad_not_counted_in_progress_advertisers() -> None:
    from datetime import UTC, datetime

    from adscout.models import Ad, ScrapeProgress
    from adscout.sources.base import emit_progress

    def mk(i: str, pid: str, name: str) -> Ad:
        return Ad(
            source="s",
            ad_archive_id=i,
            page_id=pid,
            page_name=name,
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            is_active=True,
        )

    seen: list[ScrapeProgress] = []
    await emit_progress(seen.append, [mk("1", "p", "A"), mk("2", "", " ")], "parsing", "m")
    assert (seen[0].ads_found, seen[0].advertisers) == (2, 1)


async def test_cancel_on_loop_is_scheduled_not_set_inline() -> None:
    # A signal handler can run cancel() on the loop thread mid-callback, so even an
    # on-loop cancel goes through call_soon_threadsafe rather than setting inline.
    t = CancelToken()
    waiter = asyncio.ensure_future(t.wait())
    await asyncio.sleep(0)
    t.cancel()
    assert t.cancelled
    assert t._event is not None and not t._event.is_set()
    await asyncio.wait_for(waiter, 1)
    assert t._event.is_set()


def test_cancel_from_another_thread_wakes_waiter() -> None:
    async def main() -> None:
        t = CancelToken()
        waiter = asyncio.ensure_future(t.wait())
        await asyncio.sleep(0)
        thread = threading.Thread(target=t.cancel)
        thread.start()
        await asyncio.wait_for(waiter, 1)
        thread.join()

    asyncio.run(main())


def test_cancel_after_loop_closed_is_safe() -> None:
    t = CancelToken()

    async def main() -> None:
        t._get_event()

    asyncio.run(main())
    t.cancel()
    assert t.cancelled


def test_advertiser_url_page_view_params() -> None:
    q = SearchQuery(kind="advertiser", page_id="1234567", keyword="Alpha Store")
    assert build_library_url(q) == (
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL"
        "&view_all_page_id=1234567&search_type=page&media_type=all"
    )
    gb = SearchQuery(kind="advertiser", page_id="9", keyword="A", country="GB", media_type="video")
    qs = parse_qs(urlparse(build_library_url(gb)).query)
    assert qs["country"] == ["GB"] and qs["media_type"] == ["video"]
    assert "q" not in qs


def test_sources_support_advertiser_kind() -> None:
    from pydantic import SecretStr

    from adscout.config import Settings
    from adscout.sources.meta_api import MetaApiSource
    from adscout.sources.meta_web.source import MetaWebSource

    adv = SearchQuery(kind="advertiser", page_id="1", keyword="A")
    web = MetaWebSource(Settings(_env_file=None))
    assert web.supports(adv)
    assert web.supports(adv.model_copy(update={"country": "AU"}))
    # The official API source only runs keyword searches.
    assert not MetaApiSource(SecretStr("token")).supports(adv.model_copy(update={"country": "GB"}))
