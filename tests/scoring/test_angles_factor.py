from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from adscout.models import Ad
from adscout.scoring import AngleInput, Override, angles, leader_angles, score_ads

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


THREE = AngleInput(
    angles=[
        ("Back pain relief", ["0", "1", "2"]),
        ("Confidence", ["3", "4"]),
        ("Desk workers", ["1", "5"]),
        ("One-off gift", ["0"]),
    ]
)


def test_angles_counts_only_angles_with_two_relevant_ads() -> None:
    f = angles({"0", "1", "2", "3", "4", "5"}, THREE)
    assert (f.key, f.value, f.display, f.status) == ("angles", 3, "3 angles", "pass")
    assert f.score is not None and abs(f.score - 0.7) < 1e-9
    assert f.points == 17.5
    assert f.detail == "Back pain relief, Confidence, Desk workers"

    # Excluding ad 4 leaves "Confidence" with one relevant ad: it no longer counts.
    f = angles({"0", "1", "2", "3", "5"}, THREE)
    assert (f.value, f.status) == (2, "warn")
    assert f.detail == "Back pain relief, Desk workers"

    f = angles(set(), THREE)
    assert (f.value, f.status, f.score) == (0, "fail", 0.0)
    assert f.display == "0 angles"


def test_angles_detail_is_top_three_by_relevant_ads() -> None:
    ai = AngleInput(
        angles=[
            ("Small", ["a", "b"]),
            ("Big", ["a", "b", "c", "d"]),
            ("Medium", ["a", "b", "c"]),
            ("Also small", ["c", "d"]),
        ]
    )
    f = angles({"a", "b", "c", "d"}, ai)
    assert f.value == 4
    assert f.detail == "Big, Medium, Small"


def test_angles_counts_distinct_ads() -> None:
    f = angles({"0", "1"}, AngleInput(angles=[("Dup", ["0", "0"])]))
    assert f.value == 0


def test_leader_angles_rule() -> None:
    f = leader_angles({"1", "3", "5"}, THREE)
    # Leader ads 1, 3, 5 touch Back pain relief, Confidence and Desk workers (one ad is enough).
    assert (f.value, f.display, f.status) == (3, "3 angles", "pass")
    f = leader_angles({"0"}, THREE)
    assert f.value == 2
    assert f.status == "warn"
    assert leader_angles(set(), THREE).value == 0


def test_golden_with_three_angles_scores_79_not_provisional() -> None:
    s = score_ads(_golden(), KEYWORD, [], NOW, UTC_TZ, angle_input=THREE)
    assert s.total == 79  # round(100 * (30 + 20 + 11.786 + 25 * 0.7) / 100)
    assert s.provisional is False
    ang = s.factors[-1]
    assert (ang.key, ang.value, ang.status, ang.points) == ("angles", 3, "pass", 17.5)
    assert s.leader is not None
    assert s.leader.factors[-1].status != "pending"


def test_without_angle_input_stays_provisional() -> None:
    s = score_ads(_golden(), KEYWORD, [], NOW, UTC_TZ)
    assert s.total == 82
    assert s.provisional is True
    assert s.factors[-1].status == "pending"


def test_overrides_refilter_stored_angles() -> None:
    s = score_ads(
        _golden(),
        KEYWORD,
        [Override(scope="ad", target_id="4", mode="exclude")],
        NOW,
        UTC_TZ,
        angle_input=THREE,
    )
    assert s.factors[-1].value == 2
    assert s.provisional is False


def test_display_pluralises() -> None:
    one = angles({"0", "1"}, AngleInput(angles=[("Solo", ["0", "1"])]))
    assert one.display == "1 angle"
    assert angles(set(), THREE).display == "0 angles"


def _group(prefix: str, n: int, page: str, month: int) -> list[Ad]:
    """``n`` ads with the same copy: one creative. The first is the oldest (the lead)."""
    return [
        Ad(
            source="meta_web",
            ad_archive_id=f"{prefix}{i}",
            page_id=page,
            page_name=f"Page {page}",
            start_date=datetime(2026, month, 1, tzinfo=UTC) + timedelta(hours=i),
            is_active=True,
            body=f"Posture corrector, story {prefix}",
        )
        for i in range(n)
    ]


def test_excluding_only_the_lead_keeps_the_creative_in_its_angle() -> None:
    ads = _group("x", 2, "A", 3) + _group("y", 2, "B", 4)
    ai = AngleInput(angles=[("Back pain", ["x0", "y0"])])
    s = score_ads(ads, KEYWORD, [], NOW, UTC_TZ, angle_input=ai)
    assert s.factors[-1].value == 1
    s = score_ads(
        ads,
        KEYWORD,
        [Override(scope="ad", target_id="x0", mode="exclude")],
        NOW,
        UTC_TZ,
        angle_input=ai,
    )
    assert s.factors[-1].value == 1  # x1 is still relevant, so creative x still counts
    s = score_ads(
        ads,
        KEYWORD,
        [
            Override(scope="ad", target_id="x0", mode="exclude"),
            Override(scope="ad", target_id="x1", mode="exclude"),
        ],
        NOW,
        UTC_TZ,
        angle_input=ai,
    )
    assert s.factors[-1].value == 0  # only creative y is left: below the 2-creative bar


def test_forty_ads_of_one_creative_count_once() -> None:
    ads = _group("z", 40, "A", 3) + _group("w", 1, "B", 4)
    s = score_ads(
        ads,
        KEYWORD,
        [],
        NOW,
        UTC_TZ,
        angle_input=AngleInput(angles=[("Big", ["z0"]), ("Pair", ["z0", "w0"])]),
    )
    ang = s.factors[-1]
    assert ang.value == 1
    assert ang.detail == "Pair"


def test_leader_rule_uses_still_relevant_creatives() -> None:
    ads = _group("x", 2, "A", 3) + _group("q", 1, "A", 5) + _group("y", 1, "B", 4)
    ai = AngleInput(angles=[("Back pain", ["x0", "y0"]), ("Gift", ["y0"])])
    s = score_ads(
        ads,
        KEYWORD,
        [Override(scope="ad", target_id="x0", mode="exclude")],
        NOW,
        UTC_TZ,
        angle_input=ai,
    )
    assert s.leader is not None and s.leader.advertiser_key == "A"
    assert s.leader.factors[-1].value == 1  # Back pain via x1; Gift has no A creative
