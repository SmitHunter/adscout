from datetime import UTC, datetime

import pytest

from adscout.models import Ad
from adscout.scoring import normalise_keyword
from adscout.scoring.relevance import Override, decide_relevance, is_keyword_match, tokens


def _ad(ad_id: str = "a1", page_id: str = "p1", **kw: object) -> Ad:
    return Ad(
        source="meta_web",
        ad_archive_id=ad_id,
        page_id=page_id,
        page_name="Shop",
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        is_active=True,
        **kw,
    )


def test_tokens_fold_accents_and_stem() -> None:
    assert tokens("Crème Posture-Correctors") == ["creme", "posture", "corrector"]
    assert tokens("glasses boxes")[1] == "box"
    assert tokens("glass") == ["glass"]


def test_keyword_matches_across_fields() -> None:
    ad = _ad(body="hello", link_url="https://shop.com/posture-corrector")
    assert is_keyword_match(ad, "posture corrector")


def test_hyphen_and_plural_match() -> None:
    assert is_keyword_match(_ad(body="Knee brace sleeve"), "knee-brace")
    assert is_keyword_match(_ad(body="best posture corrector"), "posture correctors")


def test_missing_token_not_relevant() -> None:
    assert not is_keyword_match(_ad(body="Knee sleeve"), "knee brace")


def test_precedence_ad_include_beats_advertiser_exclude() -> None:
    ad = _ad(body="unrelated")
    ovs = [
        Override(scope="advertiser", target_id="p1", mode="exclude"),
        Override(scope="ad", target_id="a1", mode="include"),
    ]
    d = decide_relevance([ad], "posture", ovs)["a1"]
    assert d.relevant
    assert d.reason == "ad_override"


def test_advertiser_exclude_beats_keyword() -> None:
    ad = _ad(body="posture corrector")
    ovs = [Override(scope="advertiser", target_id="p1", mode="exclude")]
    d = decide_relevance([ad], "posture", ovs)["a1"]
    assert not d.relevant
    assert d.reason == "advertiser_override"


def test_keyword_reasons() -> None:
    res = decide_relevance([_ad("a1", body="posture"), _ad("a2", body="x")], "posture", [])
    assert (res["a1"].relevant, res["a1"].reason) == (True, "keyword")
    assert (res["a2"].relevant, res["a2"].reason) == (False, "no_keyword")


def test_cyrillic_keyword_tokens() -> None:
    assert tokens("Корсет осанки") == ["корсет", "осанки"]


def test_empty_keyword_rejected() -> None:
    with pytest.raises(ValueError, match="no searchable words"):
        is_keyword_match(_ad(body="x"), "!!!")
    with pytest.raises(ValueError, match="no searchable words"):
        decide_relevance([_ad()], "!!!", [])


def test_stemming_plurals() -> None:
    assert tokens("cases purses houses glasses boxes watches") == [
        "case",
        "purse",
        "house",
        "glass",
        "box",
        "watch",
    ]
    assert is_keyword_match(_ad(body="Best phone cases"), "phone case")


def test_case_and_accent_insensitive_match() -> None:
    assert is_keyword_match(_ad(body="crème"), "CRÈME")
    assert is_keyword_match(_ad(body="CRÈME"), "crème")


def test_advertiser_include_beats_keyword_miss() -> None:
    ovs = [Override(scope="advertiser", target_id="p1", mode="include")]
    d = decide_relevance([_ad(body="unrelated")], "posture", ovs)["a1"]
    assert (d.relevant, d.reason) == (True, "advertiser_override")


def test_template_reason() -> None:
    ads = [
        _ad("t1", body="{{product.name}} is here"),
        _ad("t2", body="plain", title="{{ title }}"),
        _ad("t3", body="posture corrector {{x}}"),
        _ad("t4", body="{{product.name}}"),
    ]
    overrides = [Override(scope="ad", target_id="t4", mode="include")]
    out = decide_relevance(ads, "posture corrector", overrides)
    assert (out["t1"].relevant, out["t1"].reason) == (False, "template")
    assert (out["t2"].relevant, out["t2"].reason) == (False, "template")
    assert out["t3"].reason == "keyword"
    assert out["t4"].reason == "ad_override"


def test_stop_words_dropped_from_keyword() -> None:
    assert is_keyword_match(_ad(body="Posture corrector women's"), "posture corrector for women")


def test_stop_words_only_keyword_kept() -> None:
    assert is_keyword_match(_ad(body="for the win"), "for the")
    assert not is_keyword_match(_ad(body="for win"), "for the")


def test_normalise_keyword_keeps_stop_words() -> None:
    assert normalise_keyword("posture corrector for women") == "posture corrector for women"


def test_claude_off_topic_precedence() -> None:
    ads = [
        _ad("k1", "p1", body="posture corrector"),
        _ad("k2", "p1", body="posture corrector"),
        _ad("k3", "p2", body="posture corrector"),
        _ad("k4", "p3", body="posture corrector"),
        _ad("k5", "p4", body="posture corrector"),
    ]
    overrides = [
        Override(scope="ad", target_id="k2", mode="include"),
        Override(scope="advertiser", target_id="p2", mode="include"),
        Override(scope="advertiser", target_id="p3", mode="exclude"),
    ]
    out = decide_relevance(ads, "posture corrector", overrides, off_topic={"k1", "k2", "k3", "k4"})
    assert (out["k1"].relevant, out["k1"].reason) == (False, "claude_off_topic")
    assert (out["k2"].relevant, out["k2"].reason) == (True, "ad_override")
    assert (out["k3"].relevant, out["k3"].reason) == (True, "advertiser_override")
    assert (out["k4"].relevant, out["k4"].reason) == (False, "advertiser_override")
    assert (out["k5"].relevant, out["k5"].reason) == (True, "keyword")


def test_advertiser_kind_keeps_precedence() -> None:
    from datetime import UTC, datetime

    from adscout.models import Ad
    from adscout.scoring.relevance import Override, decide_relevance

    def ad(aid: str, page: str) -> Ad:
        return Ad(
            source="f",
            ad_archive_id=aid,
            page_id=page,
            page_name=page,
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            is_active=True,
            body="unrelated words",
        )

    ads = [ad("1", "p1"), ad("2", "p2"), ad("3", "p3"), ad("4", "p3")]
    overrides = [
        Override(scope="advertiser", target_id="p2", mode="exclude"),
        Override(scope="ad", target_id="4", mode="include"),
    ]
    out = decide_relevance(ads, "Shop", overrides, off_topic={"3", "4"}, kind="advertiser")
    assert [(out[i].relevant, out[i].reason) for i in "1234"] == [
        (True, "keyword"),
        (False, "advertiser_override"),
        (False, "claude_off_topic"),
        (True, "ad_override"),
    ]
