import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from adscout.models import Ad
from adscout.scoring import Override, score_ads
from adscout.sources.meta_web.parse_graphql import parse_payloads

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meta_web" / "posture-corrector-US"
NOW = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
UTC_TZ = ZoneInfo("UTC")
KEYWORD = "posture corrector"

STARTS = [(3, 1), (5, 10), (7, 1), (8, 15), (9, 10), (10, 5)]


def _ad(aid: str, month: int, day: int, page: str) -> Ad:
    return Ad(
        source="meta_web",
        ad_archive_id=aid,
        page_id=page,
        page_name=f"Page {page}",
        start_date=datetime(2026, month, day, tzinfo=UTC),
        is_active=True,
        body=f"Best posture corrector number {aid}",
    )


def _golden() -> list[Ad]:
    return [_ad(str(i), m, d, "A" if i % 2 else "B") for i, (m, d) in enumerate(STARTS)]


def test_golden_six_ads() -> None:
    s = score_ads(_golden(), KEYWORD, [], NOW, UTC_TZ)
    assert s.total == 82
    assert s.provisional is True
    assert [f.key for f in s.factors] == ["longevity", "continuity", "volume", "angles"]
    lon, con, vol, ang = s.factors
    assert (lon.value, lon.score, lon.points) == (230, 1.0, 30.0)
    assert (con.value, con.score, con.points) == (5, 1.0, 20.0)
    assert vol.value == 6
    assert vol.score is not None and abs(vol.score - (0.3 + 0.4 * 3 / 7)) < 1e-9
    assert vol.points == 11.8
    assert ang.status == "pending"
    assert s.advertisers == 2
    assert s.saturation == "early"
    assert s.relevant_ads == 6
    assert s.excluded_ads == 0
    assert s.creatives == 6
    assert s.warnings == ["Early: only 2 advertiser(s) — demand not yet proven."]
    assert len(s.decisions) == 6


def test_zero_relevant_ads_scores_zero_with_warning() -> None:
    ads = [_ad("1", 3, 1, "A")]
    s = score_ads(ads, "unrelated thing", [], NOW, UTC_TZ)
    assert s.total == 0
    assert s.relevant_ads == 0
    assert s.excluded_ads == 1
    assert s.creatives == 0
    assert s.warnings == ["No relevant ads — check the keyword or your exclusions."]
    assert all(f.status == "fail" for f in s.factors if f.key != "angles")
    assert all(f.value == 0 for f in s.factors if f.key != "angles")
    assert s.provisional is True


def test_excluded_advertiser_changes_score() -> None:
    ads = _golden()
    base = score_ads(ads, KEYWORD, [], NOW, UTC_TZ)
    s = score_ads(
        ads, KEYWORD, [Override(scope="advertiser", target_id="B", mode="exclude")], NOW, UTC_TZ
    )
    assert s.relevant_ads == 3
    assert s.excluded_ads == 3
    assert s.advertisers == 1
    assert s.total != base.total


def test_crowded_warning() -> None:
    ads = [_ad(str(i), 10, 1, f"P{i}") for i in range(16)]
    s = score_ads(ads, KEYWORD, [], NOW, UTC_TZ)
    assert s.saturation == "crowded"
    assert s.warnings == ["Crowded: 16 advertisers — proven demand, hard to stand out."]


def test_leader_is_advertiser_with_most_creatives() -> None:
    ads = [_ad("a1", 3, 1, "A"), _ad("a2", 5, 1, "A"), _ad("b1", 4, 1, "B")]
    ads += [_ad("b2", 6, 1, "B"), _ad("b3", 7, 1, "B"), _ad("c1", 8, 1, "C")]
    s = score_ads(ads, KEYWORD, [], NOW, UTC_TZ)
    assert s.leader is not None
    assert (s.leader.advertiser_key, s.leader.page_name) == ("B", "Page B")
    assert (s.leader.ads, s.leader.creatives) == (3, 3)
    assert [f.key for f in s.leader.factors] == ["longevity", "continuity", "volume", "angles"]


def test_leader_ties_break_on_ads_then_key() -> None:
    # Golden: A and B both have 3 creatives and 3 ads -> smallest key wins.
    s = score_ads(_golden(), KEYWORD, [], NOW, UTC_TZ)
    assert s.leader is not None
    assert s.leader.advertiser_key == "A"
    # Two ads collated into one creative give B more ads at equal creatives.
    ads = [_ad("a1", 3, 1, "A"), _ad("b1", 3, 1, "B"), _ad("b2", 3, 1, "B")]
    ads = [a.model_copy(update={"collation_id": a.ad_archive_id[0]}) for a in ads]
    s = score_ads(ads, KEYWORD, [], NOW, UTC_TZ)
    assert s.leader is not None
    assert (s.leader.advertiser_key, s.leader.ads, s.leader.creatives) == ("B", 2, 1)


def test_leader_scored_with_same_formula() -> None:
    ads = [_ad(str(i), m, d, "A") for i, (m, d) in enumerate(STARTS)]
    s = score_ads(ads, KEYWORD, [], NOW, UTC_TZ)
    assert s.leader is not None
    assert s.leader.score == s.total
    assert s.leader.factors == s.factors
    assert (s.leader.ads, s.leader.creatives) == (6, 6)


def test_leader_ignores_excluded_ads() -> None:
    s = score_ads(
        _golden(),
        KEYWORD,
        [Override(scope="advertiser", target_id="A", mode="exclude")],
        NOW,
        UTC_TZ,
    )
    assert s.leader is not None
    assert s.leader.advertiser_key == "B"
    assert s.leader.score == s.total


def test_no_leader_when_no_relevant_ads() -> None:
    s = score_ads([_ad("1", 3, 1, "A")], "unrelated thing", [], NOW, UTC_TZ)
    assert s.leader is None


def _two_advertisers() -> list[Ad]:
    ads = [_ad("a1", 3, 1, "A"), _ad("a2", 5, 1, "A"), _ad("b1", 4, 1, "B")]
    return [*ads, _ad("b2", 6, 1, "B"), _ad("b3", 7, 1, "B")]


def test_headline_is_leader_score() -> None:
    s = score_ads(_two_advertisers(), KEYWORD, [], NOW, UTC_TZ)
    assert s.leader is not None
    assert s.headline == s.leader.score


def test_headline_falls_back_to_total_without_leader() -> None:
    s = score_ads([_ad("1", 3, 1, "A")], "unrelated thing", [], NOW, UTC_TZ)
    assert s.leader is None
    assert s.headline == s.total == 0


def test_headline_in_json_dump() -> None:
    s = score_ads(_two_advertisers(), KEYWORD, [], NOW, UTC_TZ)
    assert s.model_dump()["headline"] == s.headline
    assert '"headline":' in s.model_dump_json()


def test_fixture_regression() -> None:
    texts = [(FIXTURE / "page.html").read_text(encoding="utf-8")] + [
        p.read_text(encoding="utf-8") for p in sorted(FIXTURE.glob("graphql_*.json"))
    ]
    ads = parse_payloads(texts)
    meta = json.loads((FIXTURE / "meta.json").read_text(encoding="utf-8"))
    now = datetime.fromisoformat(meta["recorded_at"])
    s = score_ads(ads, KEYWORD, [], now, UTC_TZ)
    assert 0 <= s.total <= 100
    assert s.relevant_ads <= 69
    assert (s.total, s.creatives, s.relevant_ads) == (99, 46, 52)
    # 21 of 46 creatives first started >= 90 days before recording.
    assert s.factors[0].detail == "46% of creatives running ≥ 90 days"
    assert s.leader is not None
    leader = s.leader
    assert (leader.advertiser_key, leader.page_name) == ("514393445092458", "Corecare")
    assert (leader.ads, leader.creatives, leader.score) == (26, 23, 83)
    assert s.headline == 83


def test_advertiser_kind_all_ads_relevant() -> None:
    ads = [_ad("a1", 3, 1, "A"), _ad("a2", 4, 1, "A"), _ad("a3", 5, 1, "A")]
    ads[1] = ads[1].model_copy(update={"body": "Something else entirely"})
    ads[2] = ads[2].model_copy(update={"body": "{{product.name}}"})
    s = score_ads(ads, "Alpha Store", [], NOW, UTC_TZ, kind="advertiser")
    assert s.relevant_ads == 3
    assert {d.reason for d in s.decisions.values()} == {"keyword"}
    # User overrides still win.
    excluded = [Override(scope="ad", target_id="a2", mode="exclude")]
    s = score_ads(ads, "Alpha Store", excluded, NOW, UTC_TZ, kind="advertiser")
    assert (s.decisions["a2"].relevant, s.decisions["a2"].reason) == (False, "ad_override")
    assert s.relevant_ads == 2


def test_advertiser_kind_accepts_name_without_words() -> None:
    s = score_ads([_ad("a1", 3, 1, "A")], "★★★", [], NOW, UTC_TZ, kind="advertiser")
    assert s.relevant_ads == 1
