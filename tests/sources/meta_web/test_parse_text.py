import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adscout.sources.meta_web.parse_graphql import parse_payloads
from adscout.sources.meta_web.parse_text import parse_inner_text

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "meta_web"


def _text(folder: str) -> str:
    return (FIXTURES / folder / "inner_text.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("folder", ["posture-corrector-US", "heated-eye-mask-US"])
def test_count_matches_library_ids_with_dates(folder: str) -> None:
    text = _text(folder)
    ads = parse_inner_text(text)
    assert len(ads) == text.count("Library ID:") == text.count("Started running on")
    assert all(a.is_active for a in ads)
    assert all(a.source == "meta_web_text" for a in ads)


def test_empty_search_yields_nothing() -> None:
    assert parse_inner_text(_text("zzqxwv-nothing-US")) == []


def test_hand_checked_ad() -> None:
    ads = {a.ad_archive_id: a for a in parse_inner_text(_text("posture-corrector-US"))}
    ad = ads["2275498076149743"]
    assert ad.page_name == "Axzenia"
    assert ad.page_id == ""
    assert ad.start_date == datetime(2024, 10, 31, tzinfo=UTC)
    assert ad.end_date is None
    assert ad.is_active is True
    assert ad.collation_count == 2
    assert ad.body.startswith("Suffering from scoliosis")
    assert "Order yours today and save 40% or more at axzenia.com" in ad.body
    assert "0:00 / 0:08" not in ad.body
    assert ad.title is None
    assert ad.raw["text"].startswith(" 2275498076149743")
    assert ads["948652557092896"].collation_count == 1


def test_consistent_with_graphql_parser() -> None:
    folder = "heated-eye-mask-US"
    gql = {a.ad_archive_id: a for a in parse_payloads(_all_graphql(folder))}
    text_ads = parse_inner_text(_text(folder))
    common = [a for a in text_ads if a.ad_archive_id in gql]
    assert common
    for a in common:
        assert a.page_name == gql[a.ad_archive_id].page_name.strip()
        assert a.start_date.date() == gql[a.ad_archive_id].start_date.date() or (
            abs((a.start_date - gql[a.ad_archive_id].start_date).days) <= 1
        )


def _all_graphql(folder: str) -> list[str]:
    d = FIXTURES / folder
    return [(d / "page.html").read_text(encoding="utf-8")] + [
        p.read_text(encoding="utf-8") for p in sorted(d.glob("graphql_*.json"))
    ]


SYNTH = """header
Inactive
Library ID: 111
Started running on Oct 5, 2024
Platforms
Open Drop-down
Shop A
Sponsored
First line
second line
Active
Library ID: 222
5 Oct 2024 - 12 Oct 2024
Started running on 5 Oct 2024
Shop B
Sponsored
Body B
Active
Library ID: 333
Platforms
Shop C
Sponsored
no date here
"""


def test_synthetic_status_formats_and_range() -> None:
    ads = {a.ad_archive_id: a for a in parse_inner_text(SYNTH)}
    assert set(ads) == {"111", "222"}
    a = ads["111"]
    assert a.is_active is False
    assert a.start_date == datetime(2024, 10, 5, tzinfo=UTC)
    assert a.body == "First line\nsecond line"
    assert a.page_name == "Shop A"
    b = ads["222"]
    assert b.is_active is True
    assert b.body == "Body B"


def test_range_sets_end_date() -> None:
    text = (
        "Inactive\nLibrary ID: 9\n5 Oct 2024 - 12 Oct 2024\nStarted running on 5 Oct 2024\n"
        "Adv\nSponsored\nHi\n"
    )
    (ad,) = parse_inner_text(text)
    assert ad.end_date == datetime(2024, 10, 12, tzinfo=UTC)
    assert ad.is_active is False


def test_chunk_without_date_skipped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        ads = parse_inner_text("Active\nLibrary ID: 333\nPlatforms\nShop\nSponsored\nx\n")
    assert ads == []
    assert "333" in caplog.text


@pytest.mark.parametrize("cta", ["Shop now", "learn more", "Subscribe", "Contact us"])
def test_text_parser_strips_trailing_cta(cta: str) -> None:
    text = (
        "Active\nLibrary ID: 444\nStarted running on 21 Sep 2026\nPlatforms\n"
        f"Shop\nSponsored\nBuy this thing\nIt is great\n{cta}\n"
    )
    (ad,) = parse_inner_text(text)
    assert ad.body == "Buy this thing\nIt is great"
