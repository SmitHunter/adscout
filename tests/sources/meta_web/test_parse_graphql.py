import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from adscout.sources.meta_web.parse_graphql import (
    extract_embedded_json,
    find_ad_nodes,
    iter_json_documents,
    node_to_ad,
    parse_payloads,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "meta_web"
ID_RE = re.compile(r'"ad_archive_id":"(\d+)"')


def _read(folder: str, name: str) -> str:
    return (FIXTURES / folder / name).read_text(encoding="utf-8")


def _folder_texts(folder: str) -> list[str]:
    paths = sorted((FIXTURES / folder).glob("graphql_*.json"))
    return [_read(folder, "page.html")] + [p.read_text(encoding="utf-8") for p in paths]


def _node(**over: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "body": {"text": "hello"},
        "title": "T",
        "link_url": "https://x.test",
        "caption": "x.test",
        "cta_text": "Shop now",
        "display_format": "IMAGE",
        "images": [{"original_image_url": "https://img/1.jpg"}],
        "videos": [],
        "cards": [],
    }
    node: dict[str, Any] = {
        "ad_archive_id": "1",
        "page_id": "p1",
        "page_name": "Page",
        "is_active": True,
        "start_date": 1788850800,
        "end_date": 1792134000,
        "collation_id": None,
        "collation_count": None,
        "publisher_platform": ["FACEBOOK"],
        "snapshot": snap,
    }
    node.update(over)
    return node


@pytest.mark.parametrize(
    ("folder", "expected_html", "expected_total"),
    [("posture-corrector-US", 30, 69), ("heated-eye-mask-US", 30, 90)],
)
def test_parses_all_ads_in_fixture(folder: str, expected_html: int, expected_total: int) -> None:
    texts = _folder_texts(folder)
    regex_ids = {i for t in texts for i in ID_RE.findall(t)}
    assert len(ID_RE.findall(texts[0])) >= expected_html
    assert len(regex_ids) == expected_total
    ads = parse_payloads(texts)
    assert len(ads) == expected_total
    assert {a.ad_archive_id for a in ads} == regex_ids
    for a in ads:
        assert a.start_date.tzinfo is UTC


def test_empty_search_yields_no_ads() -> None:
    assert parse_payloads([_read("zzqxwv-nothing-US", "page.html")]) == []


def test_known_ad_fields() -> None:
    ads = {
        a.ad_archive_id: a
        for a in parse_payloads([_read("posture-corrector-US", "graphql_1.json")])
    }
    ad = ads["1071475662306538"]
    assert ad.source == "meta_web"
    assert ad.page_id == "1160641857136330"
    assert ad.page_name == "The Everyday Store"
    assert ad.start_date == datetime(2026, 9, 8, 7, 0, tzinfo=UTC)
    assert ad.start_date.tzinfo is UTC
    assert ad.end_date is not None
    assert ad.end_date.tzinfo is UTC
    assert ad.is_active is True
    assert ad.collation_count == 1
    assert ad.collation_id == "972286712586370"
    assert ad.title is not None
    assert ad.title.startswith("Stand 1")
    assert ad.title.endswith("inches taller. Starting today.")
    assert ad.link_caption == "shopattheeverydaystore.co"
    assert ad.cta_text == "Shop now"
    assert ad.display_format == "VIDEO"
    assert ad.platforms[:2] == ["FACEBOOK", "INSTAGRAM"]
    assert ad.body.startswith("You're probably 1")
    assert len(ad.media) == 1
    assert ad.media[0].type == "video"
    assert ad.media[0].url.startswith("https://video.fmel16-1.fna.fbcdn.net/")
    assert ad.media[0].preview_url is not None
    assert ad.media[0].preview_url.startswith("https://scontent.")
    assert ad.raw["ad_archive_id"] == "1071475662306538"


def test_null_collation_fields_default() -> None:
    ad = node_to_ad(_node())
    assert ad is not None
    assert ad.collation_count == 1
    assert ad.collation_id is None


def test_carousel_body_falls_back_to_first_card() -> None:
    node = _node()
    node["snapshot"].update(
        body=None,
        images=[],
        display_format="CAROUSEL",
        cards=[
            {"body": "card one", "original_image_url": "https://img/c1.jpg"},
            {
                "body": "card two",
                "video_hd_url": "https://v/2.mp4",
                "video_preview_image_url": "https://img/p2.jpg",
            },
        ],
    )
    ad = node_to_ad(node)
    assert ad is not None
    assert ad.body == "card one"
    assert [(m.type, m.url) for m in ad.media] == [
        ("image", "https://img/c1.jpg"),
        ("video", "https://v/2.mp4"),
    ]
    assert ad.media[1].preview_url == "https://img/p2.jpg"


def test_body_null_without_cards_is_empty() -> None:
    node = _node()
    node["snapshot"]["body"] = None
    ad = node_to_ad(node)
    assert ad is not None
    assert ad.body == ""


def test_images_before_videos_and_sd_fallback() -> None:
    node = _node()
    node["snapshot"]["videos"] = [{"video_hd_url": None, "video_sd_url": "https://v/sd.mp4"}]
    ad = node_to_ad(node)
    assert ad is not None
    assert [(m.type, m.url) for m in ad.media] == [
        ("image", "https://img/1.jpg"),
        ("video", "https://v/sd.mp4"),
    ]


def test_missing_start_date_skipped(caplog: pytest.LogCaptureFixture) -> None:
    node = _node()
    del node["start_date"]
    with caplog.at_level(logging.WARNING):
        assert node_to_ad(node) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_template_body_kept_raw() -> None:
    node = _node()
    node["snapshot"]["body"] = {"text": "{{product.name}}"}
    node["snapshot"]["title"] = "{{product.brand}}"
    ad = node_to_ad(node)
    assert ad is not None
    assert ad.body == "{{product.name}}"
    assert ad.title == "{{product.brand}}"


def test_for_loop_prefix_and_concatenated_json() -> None:
    text = 'for (;;);{"a": 1}\n{"b": {"c": 2}}{"d": [3]}\n'
    assert list(iter_json_documents(text)) == [{"a": 1}, {"b": {"c": 2}}, {"d": [3]}]


def test_iter_json_documents_ignores_garbage() -> None:
    assert list(iter_json_documents("")) == []
    assert list(iter_json_documents("not json")) == []


def test_find_ad_nodes_is_path_agnostic() -> None:
    n = _node()
    doc = {"x": [{"y": {"z": n}}, {"ad_archive_id": "9"}]}
    assert list(find_ad_nodes(doc)) == [n]


def test_prefixed_payload_parses() -> None:
    text = "for (;;);" + json.dumps({"data": {"edges": [_node()]}})
    ads = parse_payloads([text])
    assert [a.ad_archive_id for a in ads] == ["1"]


def test_dedup_first_seen_wins() -> None:
    first = _node(page_name="First")
    second = _node(page_name="Second")
    ads = parse_payloads([json.dumps(first), json.dumps(second)])
    assert [a.page_name for a in ads] == ["First"]


def test_embedded_html_payload() -> None:
    html = _read("posture-corrector-US", "page.html")
    docs = extract_embedded_json(html)
    assert docs
    ads = parse_payloads([html])
    assert len(ads) == 30
    assert sum(1 for d in docs for _ in find_ad_nodes(d)) >= 30


@pytest.mark.parametrize("blank", [{"text": ""}, {"text": "   \n"}, "", "  "])
def test_blank_body_falls_back_to_first_card(blank: Any) -> None:
    node = _node()
    node["snapshot"].update(body=blank, cards=[{"body": {"text": "card one"}}])
    ad = node_to_ad(node)
    assert ad is not None
    assert ad.body == "card one"


@pytest.mark.parametrize("where", ["node", "snapshot"])
def test_page_name_is_stripped(where: str) -> None:
    node = _node()
    if where == "node":
        node["page_name"] = "Soovera "
    else:
        del node["page_name"]
        node["snapshot"]["page_name"] = "  Soovera\t"
    ad = node_to_ad(node)
    assert ad is not None
    assert ad.page_name == "Soovera"


def test_overflow_end_date_nulls_field() -> None:
    ad = node_to_ad(_node(end_date=10**20))
    assert ad is not None
    assert ad.end_date is None


def test_json_body_containing_script_tag_parsed() -> None:
    node = _node()
    node["snapshot"]["body"] = {"text": "use <script>alert(1)</script> here"}
    text = json.dumps({"data": {"ad": node}})
    (ad,) = parse_payloads([text])
    assert "<script" in ad.body


def test_parse_payloads_mixed_bad_and_good_nodes() -> None:
    bad = _node(ad_archive_id="bad", start_date=None)
    docs = json.dumps({"a": [_node(ad_archive_id="1"), bad, _node(ad_archive_id="2")]})
    assert [a.ad_archive_id for a in parse_payloads([docs])] == ["1", "2"]
