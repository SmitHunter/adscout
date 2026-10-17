from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import (
    create_snapshot,
    fail_snapshot,
    finish_snapshot,
    make_engine,
    set_override,
)
from adscout.db.tables import Snapshot
from adscout.models import Ad, SearchQuery
from adscout.services.report import Report, build_report, landing_domain
from adscout.sources import SearchResult

NOW = datetime(2026, 10, 15, tzinfo=UTC)
TZ = ZoneInfo("UTC")
KEYWORD = "posture corrector"


def _ad(ad_id: str, page: str, month: int, body: str | None = None, **kw: object) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=f"pid-{page.split()[0].lower()}",
        page_name=page,
        start_date=datetime(2026, month, 5, 3, 0, tzinfo=UTC),
        is_active=True,
        body=body if body is not None else f"Best posture corrector, offer {ad_id}",
        **kw,
    )


ADS = [
    _ad("1", "Alpha Store", 3, link_url="https://www.alpha.com/p"),
    _ad("2", "Alpha Store", 5, link_url="https://alpha.com/q"),
    _ad("3", "Alpha Store", 7, link_url="https://www.alpha.com/r"),
    _ad("4", "Beta Shop", 8),
    _ad("5", "Beta Shop", 9),
    _ad("6", "Gamma Co", 10, body="Yoga mats on sale"),
    _ad("7", "Delta", 10, body="{{product.name}}"),
]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _seed(engine: Engine, ads: list[Ad] = ADS, status: str = "ok") -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD, country="US"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ads, "status": status}))
        return snap.id


def test_build_report_returns_report_model(engine: Engine) -> None:
    sid = _seed(engine)
    with Session(engine) as s:
        set_override(s, KEYWORD, "advertiser", "pid-beta", "exclude")
        report = build_report(s, sid, NOW, TZ)

    assert isinstance(report, Report)
    assert report.snapshot.id == sid and report.snapshot.query == KEYWORD
    assert report.snapshot.ad_count == 7
    assert report.notice is None
    assert len(report.ads) == 7
    by_id = {a.ad_archive_id: a for a in report.ads}
    assert by_id["1"].relevant and by_id["1"].reason == "keyword"
    assert not by_id["6"].relevant and by_id["6"].reason == "no_keyword"
    assert not by_id["7"].relevant and by_id["7"].reason == "template"
    assert not by_id["4"].relevant and by_id["4"].reason == "advertiser_override"
    assert by_id["1"].creative_key.startswith("t:")
    assert by_id["1"].landing_domain == "alpha.com"
    assert by_id["4"].landing_domain is None
    assert by_id["1"].days_running == (NOW - by_id["1"].start_date).days
    assert not hasattr(by_id["1"], "raw")

    rows = {r.key: r for r in report.advertisers}
    assert report.advertisers[0].key == "pid-alpha"
    assert rows["pid-alpha"].is_leader and not rows["pid-alpha"].excluded
    assert (rows["pid-alpha"].ads, rows["pid-alpha"].creatives) == (3, 3)
    assert rows["pid-alpha"].landing_domain == "alpha.com"
    assert rows["pid-alpha"].oldest_start == datetime(2026, 3, 5, 3, tzinfo=UTC)
    assert rows["pid-alpha"].days_running_max == (NOW - rows["pid-alpha"].oldest_start).days
    assert rows["pid-beta"].excluded and not rows["pid-beta"].is_leader
    assert rows["pid-gamma"].ads == 0

    assert report.score.leader is not None and report.score.leader.advertiser_key == "pid-alpha"
    months = report.timeline_month
    assert months[0].start.isoformat() == "2026-03-01"
    assert months[0].leader_new_creatives == 1
    assert report.timeline_week and report.timeline_week[0].start.weekday() == 0
    assert sum(b.leader_new_creatives for b in months) == 3


def test_creative_key_is_group_key(engine: Engine) -> None:
    ads = [
        _ad("1", "Alpha Store", 3, collation_id="X"),
        _ad("2", "Alpha Store", 4, collation_id="X"),
    ]
    sid = _seed(engine, ads)
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    assert {a.creative_key for a in report.ads} == {"c:X"}


def test_notice_for_empty_error_snapshot(engine: Engine) -> None:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD), "fake")
        assert snap.id is not None
        fail_snapshot(s, snap.id, "boom")
        report = build_report(s, snap.id, NOW, TZ)
    assert report.notice == "This snapshot has no saved ads (error)."
    assert report.ads == [] and report.advertisers == []


@pytest.mark.parametrize("status", ["blocked", "cancelled"])
def test_notice_partial(engine: Engine, status: str) -> None:
    sid = _seed(engine, status=status)
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    assert report.notice == f"Partial data: this snapshot stopped early ({status})."


def _seed_capped(engine: Engine, message: str | None) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD, country="US"), "fake")
        assert snap.id is not None
        result = SearchResult.model_validate({"ads": ADS, "status": "capped", "message": message})
        finish_snapshot(s, snap.id, result)
        return snap.id


def test_notice_capped_uses_limit_from_message(engine: Engine) -> None:
    sid = _seed_capped(engine, "Stopped at the limit of 300 ads.")
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    assert report.notice == "Stopped at the 300 ad limit. Raise the limit to see more."


def test_notice_capped_without_message_uses_ad_count(engine: Engine) -> None:
    sid = _seed_capped(engine, None)
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    assert report.notice == f"Stopped at the {len(ADS)} ad limit. Raise the limit to see more."


def test_ad_view_carries_advertiser_key(engine: Engine) -> None:
    nameless = _ad("8", "  Nameless  ", 4).model_copy(update={"page_id": ""})
    ads = [*ADS, nameless]
    sid = _seed(engine, ads)
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    keys = {a.ad_archive_id: a.advertiser_key for a in report.ads}
    assert keys["1"] == "pid-alpha"
    assert keys["8"] == "Nameless"


def test_landing_domain_strips_www() -> None:
    assert landing_domain("https://www.Shop.com/x?y=1") == "shop.com"
    assert landing_domain("https://shop.com") == "shop.com"
    assert landing_domain(None) is None
    assert landing_domain("not a url") is None


def test_advertisers_include_irrelevant_only_and_semantics(engine: Engine) -> None:
    sid = _seed(engine)
    with Session(engine) as s:
        set_override(s, KEYWORD, "advertiser", "pid-beta", "exclude")
        report = build_report(s, sid, NOW, TZ)
    rows = {r.key: r for r in report.advertisers}
    gamma = rows["pid-gamma"]
    assert (gamma.ads, gamma.total_ads, gamma.creatives) == (0, 1, 0)
    assert not gamma.excluded and not gamma.is_leader
    assert gamma.oldest_start == datetime(2026, 10, 5, 3, tzinfo=UTC)
    beta = rows["pid-beta"]
    assert beta.excluded
    assert (beta.ads, beta.total_ads, beta.creatives) == (0, 2, 0)
    assert beta.oldest_start == datetime(2026, 8, 5, 3, tzinfo=UTC)
    assert beta.days_running_max == (NOW - beta.oldest_start).days
    alpha = rows["pid-alpha"]
    assert (alpha.ads, alpha.total_ads, alpha.creatives) == (3, 3, 3)
    keys = [r.key for r in report.advertisers]
    assert keys[0] == "pid-alpha" and set(keys) == {
        "pid-alpha",
        "pid-beta",
        "pid-gamma",
        "pid-delta",
    }


def test_ad_view_covers_ad_fields() -> None:
    from adscout.services.report import AdView

    assert set(AdView.model_fields) >= set(Ad.model_fields) - {"raw"}


def test_error_snapshot_with_ads_notice(engine: Engine) -> None:
    sid = _seed(engine)
    with Session(engine) as s:
        snap = s.get(Snapshot, sid)
        assert snap is not None
        snap.status = "error"
        snap.error_message = "Meta Ad Library scrape failed: timeout"
        s.add(snap)
        s.commit()
        report = build_report(s, sid, NOW, TZ)
    assert report.notice == (
        "This search stopped with an error: Meta Ad Library scrape failed: timeout"
    )


ADV = SearchQuery(kind="advertiser", page_id="777", keyword="Alpha Store")


def _seed_advertiser(engine: Engine) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, ADV, "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ADS, "status": "ok"}))
        return snap.id


def test_advertiser_kind_all_ads_relevant(engine: Engine) -> None:
    sid = _seed_advertiser(engine)
    with Session(engine) as s:
        set_override(s, "page:777", "ad", "2", "exclude")
        report = build_report(s, sid, NOW, TZ)
    assert (report.snapshot.kind, report.snapshot.page_id) == ("advertiser", "777")
    by_id = {a.ad_archive_id: a for a in report.ads}
    # Off-keyword and template ads count: the keyword filter doesn't apply.
    assert by_id["6"].relevant and by_id["6"].reason == "keyword"
    assert by_id["7"].relevant and by_id["7"].reason == "keyword"
    assert not by_id["2"].relevant and by_id["2"].reason == "ad_override"
    assert report.score.relevant_ads == 6


def test_advertiser_snapshot_analysis_input_bypasses_keyword(engine: Engine) -> None:
    from adscout.services.analysis import analysis_input

    sid = _seed_advertiser(engine)
    with Session(engine) as s:
        _, items = analysis_input(s, sid)
    sent = {item.id for item in items}
    assert "6" in sent  # "Yoga mats on sale" would fail the keyword filter


def test_keyword_snapshot_view_kind(engine: Engine) -> None:
    sid = _seed(engine)
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    assert (report.snapshot.kind, report.snapshot.page_id) == ("keyword", None)


def test_notice_for_queued_snapshot(engine: Engine) -> None:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD), "fake", status="queued")
        assert snap.id is not None
        report = build_report(s, snap.id, NOW, TZ)
    assert report.notice == "This search is waiting for another search to finish."


def test_snapshot_summary_from_report(engine: Engine) -> None:
    from adscout.watch.rules import summarize

    ended = _ad("8", "Alpha Store", 4).model_copy(
        update={"is_active": False, "end_date": datetime(2026, 6, 1, tzinfo=UTC)}
    )
    sid = _seed(engine, [*ADS, ended], status="capped")
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    summary = summarize(report)
    relevant = [a for a in report.ads if a.relevant]
    assert summary.id == sid and summary.status == "capped" and summary.capped
    assert summary.active_relevant == sum(1 for a in relevant if a.is_active) == 5
    from adscout.models import Ad
    from adscout.scoring.creatives import creative_key

    def raw_key(view: object) -> str:
        return creative_key(Ad.model_validate(view, from_attributes=True))

    groups: dict[str, set[str]] = {}
    for a in relevant:
        groups.setdefault(a.creative_key, set()).add(raw_key(a))
    assert sorted(map(sorted, summary.creatives)) == sorted(map(sorted, groups.values()))
    assert summary.creative_keys == {raw_key(a) for a in relevant}
    assert summary.advertisers == report.score.advertisers
    assert summary.winner == report.score.headline
    assert summary.market == report.score.total
    assert summary.started_at == report.snapshot.started_at


def test_snapshot_summary_keeps_raw_keys_of_merged_creatives(engine: Engine) -> None:
    from adscout.models.ad import MediaItem
    from adscout.watch.rules import summarize

    def img(ad_id: str, body: str) -> Ad:
        media = [MediaItem(type="image", url=f"https://x/{ad_id}.jpg", phash="f" * 16)]
        return _ad(ad_id, "Alpha Store", 3, body=body).model_copy(update={"media": media})

    # Same picture (phash), different text: one creative with two raw keys.
    sid = _seed(engine, [img("1", "posture corrector A"), img("2", "posture corrector B")])
    with Session(engine) as s:
        summary = summarize(build_report(s, sid, NOW, TZ))
    assert len(summary.creatives) == 1
    assert len(summary.creative_keys) == 2 and summary.creatives[0] == summary.creative_keys


def test_active_ad_has_no_end_date(engine: Engine) -> None:
    """Meta can report a stale end date on a running ad: the view never shows it."""
    stale = _ad("8", "Alpha Store", 4, end_date=datetime(2026, 5, 1, tzinfo=UTC))
    ended = _ad("9", "Beta Shop", 4).model_copy(
        update={"is_active": False, "end_date": datetime(2026, 6, 1, tzinfo=UTC)}
    )
    sid = _seed(engine, [stale, ended])
    with Session(engine) as s:
        report = build_report(s, sid, NOW, TZ)
    by_id = {a.ad_archive_id: a for a in report.ads}
    assert by_id["8"].is_active and by_id["8"].end_date is None
    assert by_id["9"].end_date == datetime(2026, 6, 1, tzinfo=UTC)
