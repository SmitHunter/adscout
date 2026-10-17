import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from pydantic import SecretStr

from adscout.models import ScrapeProgress, SearchQuery
from adscout.sources import CancelToken, SourceError
from adscout.sources.meta_api import EU27, MetaApiSource

FIX = Path(__file__).parent.parent / "fixtures" / "meta_api"
URL = "https://graph.facebook.com/v23.0/ads_archive"
TOKEN = "SECRETTOKEN"


def _load(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIX / name).read_text(encoding="utf-8")))


def _src(**kw: Any) -> MetaApiSource:
    return MetaApiSource(SecretStr(TOKEN), **kw)


@pytest.mark.parametrize("cc", ["DE", "FR", "GB", "UK", "EU"])
def test_supports_eu_uk(cc: str) -> None:
    assert _src().supports(SearchQuery(keyword="x", country=cc))


@pytest.mark.parametrize("cc", ["US", "AU", "CA"])
def test_supports_only_eu_uk(cc: str) -> None:
    assert not _src().supports(SearchQuery(keyword="x", country=cc))


@respx.mock
async def test_maps_fields() -> None:
    page = _load("ads_archive_page1.json")
    del page["paging"]["next"]
    route = respx.get(URL).respond(json=page)
    q = SearchQuery(keyword="posture", country="DE", media_type="video")
    result = await _src().search(q)
    assert result.status == "ok"
    a, b = result.ads
    assert a.source == "meta_api"
    assert a.ad_archive_id == "1001"
    assert a.page_id == "501"
    assert a.page_name == "Alpha Store"
    assert a.start_date == datetime(2026, 3, 2, tzinfo=UTC)
    assert a.is_active is True
    assert a.body == "Fix your posture today"
    assert a.title == "Posture Corrector"
    assert a.link_caption == "alpha.example"
    assert a.link_url is None
    assert a.media == []
    assert a.platforms == ["facebook", "instagram"]
    assert a.raw["ad_snapshot_url"].endswith("id=1001")
    assert b.start_date == datetime(2026, 2, 10, tzinfo=UTC)
    assert b.is_active is False
    assert b.body == ""
    params = route.calls[0].request.url.params
    assert params["search_terms"] == "posture"
    assert json.loads(params["ad_reached_countries"]) == ["DE"]
    assert params["ad_active_status"] == "ACTIVE"
    assert params["media_type"] == "VIDEO"
    assert params["ad_type"] == "ALL"
    assert params["limit"] == "100"
    assert "ad_snapshot_url" in params["fields"]
    assert "access_token" not in params
    assert route.calls[0].request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_eu_sends_all_eu_countries() -> None:
    route = respx.get(URL).respond(json={"data": []})
    await _src().search(SearchQuery(keyword="x", country="EU", active_status="all"))
    params = route.calls[0].request.url.params
    assert json.loads(params["ad_reached_countries"]) == sorted(EU27)
    assert params["ad_active_status"] == "ALL"


@respx.mock
async def test_follows_paging_until_limit() -> None:
    page1, page2 = _load("ads_archive_page1.json"), _load("ads_archive_page2.json")
    next_url = page1["paging"]["next"]
    respx.get(URL, params={"after": "a1"}).respond(json=page2)
    respx.get(URL).respond(json=page1)
    seen: list[ScrapeProgress] = []
    result = await _src().search(SearchQuery(keyword="x", country="GB"), progress=seen.append)
    assert next_url
    assert [a.ad_archive_id for a in result.ads] == ["1001", "1002", "1003"]
    assert result.status == "ok"
    assert [p.ads_found for p in seen] == [2, 3]


@respx.mock
async def test_limit_caps_results() -> None:
    page1 = _load("ads_archive_page1.json")
    route = respx.get(URL).respond(json=page1)
    result = await _src().search(SearchQuery(keyword="x", country="GB", limit=1))
    assert len(result.ads) == 1
    assert result.status == "capped"
    assert route.call_count == 1


@respx.mock
async def test_max_results_caps() -> None:
    respx.get(URL).respond(json=_load("ads_archive_page1.json"))
    result = await _src(max_results=2).search(SearchQuery(keyword="x", country="GB"))
    assert len(result.ads) == 2
    assert result.status == "capped"


@respx.mock
async def test_cancel_between_pages() -> None:
    page1 = _load("ads_archive_page1.json")
    route = respx.get(URL).respond(json=page1)
    cancel = CancelToken()

    async def on_progress(p: ScrapeProgress) -> None:
        cancel.cancel()

    result = await _src().search(
        SearchQuery(keyword="x", country="GB"), progress=on_progress, cancel=cancel
    )
    assert result.status == "cancelled"
    assert len(result.ads) == 2
    assert route.call_count == 1


@respx.mock
async def test_token_error_is_blocked() -> None:
    respx.get(URL).respond(
        400,
        json={
            "error": {
                "message": "Invalid OAuth access token.",
                "type": "OAuthException",
                "code": 190,
            }
        },
    )
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert result.status == "blocked"
    assert result.ads == []
    assert result.message == (
        "Meta API token rejected. Create a new token at developers.facebook.com/tools/explorer "
        "and set META_ACCESS_TOKEN in .env."
    )
    assert TOKEN not in (result.message or "")


@respx.mock
async def test_other_http_error_raises_without_token() -> None:
    respx.get(URL).respond(500, text="boom")
    with pytest.raises(SourceError) as exc:
        await _src().search(SearchQuery(keyword="x", country="GB"))
    assert TOKEN not in str(exc.value)
    assert "500" in str(exc.value)


@respx.mock
async def test_network_error_raises_without_token() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SourceError) as exc:
        await _src().search(SearchQuery(keyword="x", country="GB"))
    assert TOKEN not in str(exc.value)


@respx.mock
async def test_injected_client_not_closed() -> None:
    respx.get(URL).respond(json={"data": []})
    async with httpx.AsyncClient() as client:
        await MetaApiSource(SecretStr(TOKEN), client=client).search(
            SearchQuery(keyword="x", country="GB")
        )
        assert not client.is_closed


@respx.mock
async def test_malformed_item_skipped(caplog: pytest.LogCaptureFixture) -> None:
    good = _load("ads_archive_page1.json")["data"][0]
    items = [
        {"id": "9", "page_id": "1", "page_name": "NoDate"},
        {"page_id": "1", "page_name": "NoId", "ad_delivery_start_time": "2026-01-01"},
        {"id": "10", "page_id": "1", "page_name": "Bad", "ad_delivery_start_time": "garbage"},
        good,
    ]
    respx.get(URL).respond(json={"data": items})
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert result.status == "ok"
    assert [a.ad_archive_id for a in result.ads] == ["1001"]
    assert TOKEN not in caplog.text


@respx.mock
async def test_malformed_snapshot_url_skipped() -> None:
    items = _load("ads_archive_page1.json")["data"]
    bad = {**items[0], "id": "77", "ad_snapshot_url": "http://[bad"}
    respx.get(URL).respond(json={"data": [bad, items[1]]})
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert [a.ad_archive_id for a in result.ads] == ["1002"]


@respx.mock
async def test_token_never_in_request_urls() -> None:
    page1, page2 = _load("ads_archive_page1.json"), _load("ads_archive_page2.json")
    assert TOKEN in page1["paging"]["next"]
    respx.get(URL, params={"after": "a1"}).respond(json=page2)
    route = respx.get(URL).respond(json=page1)
    await _src().search(SearchQuery(keyword="x", country="GB"))
    assert len(respx.calls) == 2
    for call in respx.calls:
        assert TOKEN not in str(call.request.url)
        assert call.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert route.call_count == 1


@respx.mock
async def test_foreign_paging_next_not_followed(caplog: pytest.LogCaptureFixture) -> None:
    page = _load("ads_archive_page1.json")
    page["paging"]["next"] = "https://evil.example/steal?after=a1"
    respx.get(URL).respond(json=page)
    evil = respx.get("https://evil.example/steal").respond(json={"data": []})
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert not evil.called
    assert result.status == "ok"
    assert len(result.ads) == 2
    assert "outside graph.facebook.com" in caplog.text


@respx.mock
async def test_token_stripped_from_stored_raw() -> None:
    page = _load("ads_archive_page1.json")
    del page["paging"]["next"]
    assert TOKEN in page["data"][0]["ad_snapshot_url"]
    respx.get(URL).respond(json=page)
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    a = result.ads[0]
    assert TOKEN not in json.dumps(a.raw)
    assert "access_token" not in a.raw["ad_snapshot_url"]
    assert a.raw["ad_snapshot_url"] == "https://www.facebook.com/ads/archive/render_ad/?id=1001"
    assert TOKEN in page["data"][0]["ad_snapshot_url"]  # source item not mutated


@pytest.mark.parametrize("subcode", [458, 463, 467])
@respx.mock
async def test_token_subcode_is_token_rejected(subcode: int) -> None:
    respx.get(URL).respond(
        400,
        json={"error": {"type": "OAuthException", "code": 102, "error_subcode": subcode}},
    )
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert result.status == "blocked"
    assert (result.message or "").startswith("Meta API token rejected.")


@pytest.mark.parametrize("code", [4, 17, 613])
@respx.mock
async def test_rate_limit_is_refused_not_token_rejected(code: int) -> None:
    respx.get(URL).respond(
        400,
        json={
            "error": {
                "message": "Application request limit reached",
                "type": "OAuthException",
                "code": code,
            }
        },
    )
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert result.status == "blocked"
    assert result.message == (
        "Meta API refused the request. "
        f"Meta API rate limit hit (code {code}): Application request limit reached. "
        "Wait an hour and retry."
    )
    assert "token rejected" not in (result.message or "")


@respx.mock
async def test_permission_error_is_refused_with_code_and_message() -> None:
    respx.get(URL).respond(
        403,
        json={
            "error": {
                "message": f"Application does not have permission (token {TOKEN})",
                "type": "OAuthException",
                "code": 10,
            }
        },
    )
    result = await _src().search(SearchQuery(keyword="x", country="GB"))
    assert result.status == "blocked"
    msg = result.message or ""
    assert msg.startswith("Meta API refused the request. ")
    assert "code 10" in msg
    assert "Application does not have permission" in msg
    assert "token rejected" not in msg
    assert TOKEN not in msg
