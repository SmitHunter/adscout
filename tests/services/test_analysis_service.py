from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.analysis.backends import BackendName
from adscout.analysis.models import AngleAnalysis
from adscout.analysis.prompt import input_hash
from adscout.db.repo import create_snapshot, finish_snapshot, make_engine, set_override
from adscout.models import Ad, SearchQuery
from adscout.services.analysis import analysis_input, angle_input_for, run_analysis
from adscout.services.report import build_report
from adscout.sources import SearchResult

NOW = datetime(2026, 10, 15, tzinfo=UTC)
TZ = ZoneInfo("UTC")
KEYWORD = "posture corrector"
OFF_TOPIC_ONE = (
    "Claude marked 1 ad as not selling this product. It's excluded from the score. "
    "Review it in the Excluded tab."
)


def _ad(ad_id: str, page: str, month: int, body: str | None = None) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=f"pid-{page.split()[0].lower()}",
        page_name=page,
        start_date=datetime(2026, month, 5, 3, 0, tzinfo=UTC),
        is_active=True,
        body=body if body is not None else f"Best posture corrector, offer {ad_id}",
    )


ADS = [
    _ad("1", "Alpha Store", 3),
    _ad("2", "Alpha Store", 5),
    _ad("3", "Alpha Store", 7),
    _ad("4", "Beta Shop", 8),
    _ad("5", "Beta Shop", 9),
    _ad("6", "Gamma Co", 10, body="Yoga mats on sale"),
    _ad("7", "Delta Mart", 6, body="Posture corrector not working? Try our massage gun"),
]

RESULT: dict[str, Any] = {
    "angles": [
        {
            "name": "Back pain relief",
            "description": "Relief from desk-job back pain.",
            "emotion": "pain_relief",
            "ad_ids": ["1", "2", "3", "zzz-unknown"],
        },
        {
            "name": "Confidence",
            "description": "Stand tall.",
            "emotion": "confidence",
            "ad_ids": ["4", "5"],
        },
        {
            "name": "Gift",
            "description": "A gift for dad.",
            "emotion": "gift",
            "ad_ids": ["2", "5"],
        },
    ],
    "hooks": [{"text": "Still sore after work?", "ad_ids": ["1"]}],
    "offers": [{"kind": "discount", "text": "50% off today", "ad_ids": ["2"]}],
    "not_product_ad_ids": ["7"],
    "seasonality": "evergreen",
    "seasonality_note": "No seasonal cues.",
    "verdict": "One. Two. Three.",
}


class FakeAnalyzer:
    def __init__(
        self,
        model: str = "claude-sonnet-5-5",
        usage: dict[str, Any] | None = None,
        name: BackendName = "cli",
        result: dict[str, Any] | None = None,
    ) -> None:
        self.name: BackendName = name
        self.result = result if result is not None else RESULT
        self.model = model
        self.calls: list[tuple[str, str]] = []
        self.usage = usage if usage is not None else {"input_tokens": 10, "output_tokens": 5}

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        self.calls.append((system, user))
        return AngleAnalysis.model_validate(self.result), self.usage


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _seed(engine: Engine, ads: list[Ad] = ADS) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD, country="US"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ads, "status": "ok"}))
        return snap.id


async def test_run_analysis_cleans_and_saves(engine: Engine) -> None:
    sid = _seed(engine)
    fake = FakeAnalyzer(usage={"input_tokens": 1, "output_tokens": 2, "cache_read": 9})
    row = await run_analysis(engine, sid, fake, NOW)
    assert len(fake.calls) == 1
    _system, user = fake.calls[0]
    assert "<ads>" in user and "Yoga" not in user  # only relevant ads are sent
    assert row.snapshot_id == sid
    assert (row.model, row.backend, row.prompt_version) == ("claude-sonnet-5-5", "cli", "angles-v2")
    assert row.ad_count == 6
    assert row.usage == {"input_tokens": 1, "output_tokens": 2}
    result = AngleAnalysis.model_validate(row.result)
    assert result.angles[0].ad_ids == ["1", "2", "3"]  # unknown id dropped by clean


async def test_usage_missing_fields_are_none(engine: Engine) -> None:
    sid = _seed(engine)
    row = await run_analysis(engine, sid, FakeAnalyzer(usage={"inputTokens": 3}), NOW)
    assert row.usage == {"input_tokens": None, "output_tokens": None}


async def test_usage_keeps_cache_token_counts(engine: Engine) -> None:
    sid = _seed(engine)
    cli_usage = {
        "input_tokens": 4,
        "cache_creation_input_tokens": 2100,
        "cache_read_input_tokens": 13000,
        "output_tokens": 900,
        "server_tool_use": {"web_search_requests": 0},
        "service_tier": "standard",
    }
    row = await run_analysis(engine, sid, FakeAnalyzer(usage=cli_usage), NOW)
    assert row.usage == {
        "input_tokens": 4,
        "output_tokens": 900,
        "cache_creation_input_tokens": 2100,
        "cache_read_input_tokens": 13000,
    }


async def test_cache_hit_skips_analyzer(engine: Engine) -> None:
    sid = _seed(engine)
    fake = FakeAnalyzer()
    first = await run_analysis(engine, sid, fake, NOW)
    second = await run_analysis(engine, sid, fake, NOW)
    assert len(fake.calls) == 1
    assert second.id == first.id
    # Another model is a different cache key.
    opus = FakeAnalyzer(model="claude-opus-5-5")
    third = await run_analysis(engine, sid, opus, NOW)
    assert len(opus.calls) == 1
    assert third.id != first.id


async def test_cache_survives_later_days(engine: Engine) -> None:
    sid = _seed(engine)
    fake = FakeAnalyzer()
    first = await run_analysis(engine, sid, fake, NOW)
    later = await run_analysis(engine, sid, fake, NOW + timedelta(days=3))
    assert len(fake.calls) == 1
    assert later.id == first.id


async def test_force_rebills(engine: Engine) -> None:
    sid = _seed(engine)
    fake = FakeAnalyzer()
    first = await run_analysis(engine, sid, fake, NOW)
    again = await run_analysis(engine, sid, fake, NOW, force=True)
    assert len(fake.calls) == 2
    assert again.id == first.id  # same cache key: the row is replaced, not duplicated


async def test_zero_relevant_raises(engine: Engine) -> None:
    sid = _seed(engine, [_ad("6", "Gamma Co", 10, body="Yoga mats on sale")])
    fake = FakeAnalyzer()
    with pytest.raises(ValueError, match=r"No relevant ads to analyze."):
        await run_analysis(engine, sid, fake, NOW)
    assert fake.calls == []


async def test_missing_snapshot_raises_lookup(engine: Engine) -> None:
    with pytest.raises(LookupError):
        await run_analysis(engine, 999, FakeAnalyzer(), NOW)


async def test_angle_input_for(engine: Engine) -> None:
    sid = _seed(engine)
    row = await run_analysis(engine, sid, FakeAnalyzer(), NOW)
    ai = angle_input_for(row)
    assert ai.angles == [
        ("Back pain relief", ["1", "2", "3"]),
        ("Confidence", ["4", "5"]),
        ("Gift", ["2", "5"]),
    ]


async def test_report_includes_analysis_and_not_provisional(engine: Engine) -> None:
    sid = _seed(engine)
    with Session(engine) as s:
        before = build_report(s, sid, NOW, TZ)
    assert before.analysis is None
    assert before.score.provisional is True

    await run_analysis(engine, sid, FakeAnalyzer(), NOW)
    with Session(engine) as s:
        rep = build_report(s, sid, NOW, TZ)
    assert rep.score.provisional is False
    assert rep.analysis is not None
    assert rep.analysis.model == "claude-sonnet-5-5"
    assert rep.analysis.backend == "cli"
    assert rep.analysis.ad_count == 6
    assert rep.analysis.created_at.tzinfo is not None
    assert rep.analysis.result.verdict == "One. Two. Three."
    ang = rep.score.factors[-1]
    assert (ang.value, ang.display, ang.status) == (3, "3 angles", "pass")
    assert ang.detail == "Back pain relief, Confidence, Gift"
    assert [(a.name, a.creatives, a.ads) for a in rep.analysis.angles] == [
        ("Back pain relief", 3, 3),
        ("Confidence", 2, 2),
        ("Gift", 2, 2),
    ]


async def test_overrides_recompute_angles_without_new_call(engine: Engine) -> None:
    sid = _seed(engine)
    fake = FakeAnalyzer()
    await run_analysis(engine, sid, fake, NOW)
    with Session(engine) as s:
        set_override(s, KEYWORD, "ad", "5", "exclude")
        rep = build_report(s, sid, NOW, TZ)
    assert len(fake.calls) == 1
    ang = rep.score.factors[-1]
    assert ang.value == 1  # Confidence and Gift fall to one relevant ad each
    assert ang.detail == "Back pain relief"
    assert rep.analysis is not None
    assert [(a.name, a.creatives, a.ads) for a in rep.analysis.angles] == [
        ("Back pain relief", 3, 3),
        ("Confidence", 1, 1),
        ("Gift", 1, 1),
    ]
    # The stored result is untouched.
    assert rep.analysis.result.angles[1].ad_ids == ["4", "5"]


async def test_flagged_ads_excluded_at_scoring_time(engine: Engine) -> None:
    sid = _seed(engine)
    await run_analysis(engine, sid, FakeAnalyzer(), NOW)
    with Session(engine) as s:
        rep = build_report(s, sid, NOW, TZ)
        assert rep.analysis is not None
        assert rep.analysis.flagged_off_topic == 1
        assert rep.score.decisions["7"].relevant is False
        assert rep.score.decisions["7"].reason == "claude_off_topic"
        assert [a.reason for a in rep.ads if a.ad_archive_id == "7"] == ["claude_off_topic"]
        assert OFF_TOPIC_ONE in rep.score.warnings
        # A user exclude takes over the reason; nothing is flagged any more.
        set_override(s, KEYWORD, "ad", "7", "exclude")
        rep = build_report(s, sid, NOW, TZ)
    assert rep.analysis is not None
    assert rep.analysis.flagged_off_topic == 0
    assert rep.score.decisions["7"].reason == "ad_override"
    assert not any("Claude marked" in w for w in rep.score.warnings)


async def test_flagged_ids_never_count_toward_angles(engine: Engine) -> None:
    sid = _seed(engine)
    flagged = {**RESULT, "not_product_ad_ids": ["7", "3"]}
    await run_analysis(engine, sid, FakeAnalyzer(result=flagged), NOW)
    with Session(engine) as s:
        rep = build_report(s, sid, NOW, TZ)
    assert rep.analysis is not None
    assert rep.analysis.result.angles[0].ad_ids == ["1", "2"]
    assert rep.analysis.flagged_off_topic == 2
    assert (
        "Claude marked 2 ads as not selling this product. They're excluded from the score. "
        "Review them in the Excluded tab."
    ) in rep.score.warnings


# Snapshot 2's shape: the advertiser with the most creatives is one Claude flagged entirely.
LEADER_FLAGGED = [
    *ADS[:5],
    *(
        _ad(f"d{i}", "Delta Mart", 6, body=f"Posture corrector? Try our massage gun {i}")
        for i in range(4)
    ),
]
LEADER_RESULT: dict[str, Any] = {
    **RESULT,
    "not_product_ad_ids": ["d0", "d1", "d2", "d3"],
}


async def test_fully_flagged_leader_loses_the_lead(engine: Engine) -> None:
    sid = _seed(engine, LEADER_FLAGGED)
    with Session(engine) as s:
        before = build_report(s, sid, NOW, TZ)
    assert before.score.leader is not None
    assert before.score.leader.page_name == "Delta Mart"

    await run_analysis(engine, sid, FakeAnalyzer(result=LEADER_RESULT), NOW)
    with Session(engine) as s:
        rep = build_report(s, sid, NOW, TZ)
    assert rep.score.leader is not None
    assert rep.score.leader.page_name == "Alpha Store"
    assert rep.analysis is not None and rep.analysis.flagged_off_topic == 4
    assert (
        "Claude marked 4 ads as not selling this product. They're excluded from the score. "
        "Review them in the Excluded tab."
    ) in rep.score.warnings
    delta = next(a for a in rep.advertisers if a.name == "Delta Mart")
    assert (delta.ads, delta.is_leader) == (0, False)
    assert rep.score.relevant_ads == 5


async def test_user_ad_include_beats_claude_flag(engine: Engine) -> None:
    sid = _seed(engine, LEADER_FLAGGED)
    await run_analysis(engine, sid, FakeAnalyzer(result=LEADER_RESULT), NOW)
    with Session(engine) as s:
        set_override(s, KEYWORD, "ad", "d0", "include")
        rep = build_report(s, sid, NOW, TZ)
    assert (rep.score.decisions["d0"].relevant, rep.score.decisions["d0"].reason) == (
        True,
        "ad_override",
    )
    assert rep.score.decisions["d1"].reason == "claude_off_topic"
    assert rep.analysis is not None and rep.analysis.flagged_off_topic == 3


async def test_user_advertiser_include_beats_claude_flag(engine: Engine) -> None:
    sid = _seed(engine, LEADER_FLAGGED)
    await run_analysis(engine, sid, FakeAnalyzer(result=LEADER_RESULT), NOW)
    with Session(engine) as s:
        set_override(s, KEYWORD, "advertiser", "pid-delta", "include")
        rep = build_report(s, sid, NOW, TZ)
    assert all(rep.score.decisions[f"d{i}"].relevant for i in range(4))
    assert rep.score.leader is not None and rep.score.leader.page_name == "Delta Mart"
    assert rep.analysis is not None and rep.analysis.flagged_off_topic == 0
    assert not any("Claude marked" in w for w in rep.score.warnings)


async def test_flags_do_not_change_the_analysis_input(engine: Engine) -> None:
    sid = _seed(engine, LEADER_FLAGGED)
    with Session(engine) as s:
        _, before = analysis_input(s, sid)
    fake = FakeAnalyzer(result=LEADER_RESULT)
    first = await run_analysis(engine, sid, fake, NOW)
    with Session(engine) as s:
        _, after = analysis_input(s, sid)
    assert input_hash(after) == input_hash(before) == first.input_hash
    assert {i.id for i in after} >= {"d0", "d1", "d2", "d3"}
    again = await run_analysis(engine, sid, fake, NOW)
    assert again.id == first.id
    assert len(fake.calls) == 1  # still a cache hit


SIBLINGS = [
    *ADS[:4],
    _ad("5", "Beta Shop", 9, body="Corrector bundle, posture corrector deal"),
    _ad("5b", "Beta Shop", 10, body="Corrector bundle, posture corrector deal"),
    _ad("5c", "Beta Shop", 10, body="Corrector bundle, posture corrector deal"),
    _ad("7", "Delta Mart", 6, body="Posture corrector not working? Try our massage gun"),
    _ad("7b", "Delta Mart", 7, body="Posture corrector not working? Try our massage gun"),
    _ad("7c", "Delta Mart", 7, body="Posture corrector not working? Try our massage gun"),
]


async def test_creative_level_angles_and_exclusions(engine: Engine) -> None:
    sid = _seed(engine, SIBLINGS)
    fake = FakeAnalyzer()
    row = await run_analysis(engine, sid, fake, NOW)
    assert row.ad_count == 6  # 5, 5b and 5c are one creative; 5 is its lead (oldest)
    assert "5b" not in fake.calls[0][1]
    with Session(engine) as s:
        rep = build_report(s, sid, NOW, TZ)
        assert rep.analysis is not None
        views = {a.name: (a.creatives, a.ads) for a in rep.analysis.angles}
        assert views["Confidence"] == (2, 4)  # creative 4, plus creative 5 with three ads
        assert rep.analysis.flagged_off_topic == 3

        # Excluding only the lead keeps the creative in its angles via its siblings.
        set_override(s, KEYWORD, "ad", "5", "exclude")
        set_override(s, KEYWORD, "ad", "7", "exclude")
        rep = build_report(s, sid, NOW, TZ)
    assert rep.analysis is not None
    views = {a.name: (a.creatives, a.ads) for a in rep.analysis.angles}
    assert views["Confidence"] == (2, 3)
    assert views["Gift"] == (2, 3)
    assert rep.score.factors[-1].value == 3
    assert rep.analysis.flagged_off_topic == 2
    assert len(fake.calls) == 1
