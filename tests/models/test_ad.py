from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from adscout.models import Ad, SearchQuery


def _ad(**kw: object) -> Ad:
    base: dict[str, object] = {
        "source": "meta_web",
        "ad_archive_id": "1",
        "page_id": "2",
        "page_name": "Shop",
        "start_date": datetime(2026, 4, 1, tzinfo=UTC),
        "end_date": None,
        "is_active": True,
    }
    base.update(kw)
    return Ad.model_validate(base)


def test_keyword_is_stripped() -> None:
    assert SearchQuery(keyword="  knee brace ").keyword == "knee brace"


@pytest.mark.parametrize("keyword", ["k", "x" * 100, "  " + "x" * 100 + "  "])
def test_keyword_length_bounds_accepted(keyword: str) -> None:
    assert SearchQuery(keyword=keyword).keyword == keyword.strip()


@pytest.mark.parametrize("keyword", ["", "   ", "x" * 101])
def test_keyword_length_bounds_rejected(keyword: str) -> None:
    with pytest.raises(ValidationError):
        SearchQuery(keyword=keyword)


@pytest.mark.parametrize("limit", [1, 300, 1000])
def test_limit_bounds_accepted(limit: int) -> None:
    assert SearchQuery(keyword="x", limit=limit).limit == limit


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_limit_bounds_rejected(limit: int) -> None:
    with pytest.raises(ValidationError):
        SearchQuery(keyword="x", limit=limit)


def test_country_must_be_two_letters() -> None:
    with pytest.raises(ValidationError):
        SearchQuery(keyword="x", country="USA")
    assert SearchQuery(keyword="x", country="au").country == "AU"
    assert SearchQuery(keyword="x", country="EU").country == "EU"


def test_days_running_active_vs_ended() -> None:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    assert _ad().days_running(now) == 199
    ended = _ad(
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        end_date=datetime(2026, 1, 31, tzinfo=UTC),
        is_active=False,
    )
    assert ended.days_running(now) == 30


def test_naive_start_date_rejected() -> None:
    with pytest.raises(ValidationError):
        _ad(start_date=datetime(2026, 4, 1))


def test_naive_end_date_rejected() -> None:
    with pytest.raises(ValidationError):
        _ad(end_date=datetime(2026, 4, 2), is_active=False)


@pytest.mark.parametrize("count", [0, -1])
def test_collation_count_must_be_positive(count: int) -> None:
    with pytest.raises(ValidationError):
        _ad(collation_count=count)


@pytest.mark.parametrize("raw", ["UK", "uk", " Uk "])
def test_country_uk_normalised_to_gb(raw: str) -> None:
    assert SearchQuery(keyword="x", country=raw).country == "GB"


@pytest.mark.parametrize("bad", ["ZZ", "XX", "AA"])
def test_country_must_be_iso_alpha2(bad: str) -> None:
    with pytest.raises(ValidationError):
        SearchQuery(keyword="x", country=bad)


@pytest.mark.parametrize("ok", ["US", "AU", "GB", "DE", "EU", "nz"])
def test_country_iso_and_eu_accepted(ok: str) -> None:
    assert SearchQuery(keyword="x", country=ok).country == ok.upper()


def test_advertiser_key() -> None:
    assert _ad(page_id="7", page_name=" X ").advertiser_key == "7"
    assert _ad(page_id="", page_name="  Shop  ").advertiser_key == "Shop"
    assert _ad(page_id="", page_name="   ").advertiser_key is None


def test_search_query_defaults_to_keyword_kind() -> None:
    q = SearchQuery(keyword="x")
    assert (q.kind, q.page_id) == ("keyword", None)


def test_advertiser_query_needs_digit_page_id() -> None:
    q = SearchQuery(kind="advertiser", page_id=" 1234567 ", keyword="Alpha Store")
    assert (q.page_id, q.keyword) == ("1234567", "Alpha Store")
    with pytest.raises(ValidationError, match="page_id"):
        SearchQuery(kind="advertiser", keyword="Alpha Store")
    with pytest.raises(ValidationError, match="page_id"):
        SearchQuery(kind="advertiser", page_id="12ab", keyword="Alpha Store")
    with pytest.raises(ValidationError, match="page_id"):
        SearchQuery(keyword="x", page_id="123")


def test_country_all_only_for_advertiser() -> None:
    assert SearchQuery(kind="advertiser", page_id="1", keyword="A").country == "ALL"
    assert SearchQuery(kind="advertiser", page_id="1", keyword="A", country="all").country == "ALL"
    assert SearchQuery(kind="advertiser", page_id="1", keyword="A", country="au").country == "AU"
    assert SearchQuery(keyword="x").country == "US"
    with pytest.raises(ValidationError):
        SearchQuery(keyword="x", country="ALL")
