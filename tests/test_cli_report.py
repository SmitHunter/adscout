import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session
from typer.testing import CliRunner

import adscout.cli as cli
from adscout.cli import app
from adscout.db.repo import create_snapshot, finish_snapshot, make_engine
from adscout.models import Ad, SearchQuery
from adscout.scoring import WinnerScore
from adscout.sources import SearchResult

NOW = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
KEYWORD = "posture corrector"


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
]


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = make_engine(tmp_path / "adscout.db")
    monkeypatch.setattr(cli, "get_engine", lambda: eng)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    return eng


def _seed(engine: Engine, ads: list[Ad] = ADS, status: str = "ok") -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD, country="US"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ads, "status": status}))
        return snap.id


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "120"})
    return result.exit_code, result.output


def _json(snapshot_id: int) -> WinnerScore:
    code, out = _invoke("report", str(snapshot_id), "--json")
    assert code == 0, out
    return WinnerScore.model_validate_json(out)


def test_report_renders_score_and_chips(engine: Engine) -> None:
    sid = _seed(engine)
    expected = _json(sid)

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    assert expected.leader is not None
    assert f"Winner Score {expected.headline}/100" in out
    assert f"Market demand {expected.total}/100" in out
    assert "PROVISIONAL — angle analysis not run yet" in out
    assert '"posture corrector"' in out and "US" in out
    for header in ("Market factors", "Value", "Fail <", "Pass ≥", "Status", "Points"):
        assert header in out
    for factor in expected.factors:
        assert factor.label in out
        assert factor.detail in out
        assert factor.status.upper() in out
    assert "PENDING" in out
    longevity = next(f for f in expected.factors if f.key == "longevity")
    assert f"{longevity.points}/{longevity.max_points}" in out
    assert "—/25" in out
    # Monthly timeline of relevant creatives: Mar..Sep 2026 (Gamma's Oct ad is irrelevant).
    assert "2026-03" in out and "2026-09" in out
    assert any(ch in out for ch in "▁▂▃▄▅▆▇█")
    # Top advertisers by relevant ads.
    assert "Alpha Store" in out and "Beta Shop" in out
    assert "Gamma Co" not in out
    assert "Partial data" not in out


def test_report_json_is_valid_winner_score(engine: Engine) -> None:
    sid = _seed(engine)

    code, out = _invoke("report", str(sid), "--json")

    assert code == 0, out
    data = json.loads(out)
    score = WinnerScore.model_validate(data)
    assert score.relevant_ads == 5
    assert score.excluded_ads == 1
    assert score.provisional is True
    assert [f.key for f in score.factors] == ["longevity", "continuity", "volume", "angles"]


def test_report_json_uses_pass_not_pass_(engine: Engine) -> None:
    sid = _seed(engine)

    code, out = _invoke("report", str(sid), "--json")

    assert code == 0, out
    assert '"pass":' in out
    assert '"pass_"' not in out
    assert json.loads(out)["factors"][0]["threshold"]["pass"] == 90


def test_report_header_leads_with_strongest_advertiser(engine: Engine) -> None:
    sid = _seed(engine)
    score = _json(sid)
    leader = score.leader
    assert leader is not None and leader.page_name == "Alpha Store"
    assert score.headline == leader.score != score.total

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    lines = out.splitlines()
    first = next(i for i, ln in enumerate(lines) if "Winner Score" in ln)
    assert f"Winner Score {leader.score}/100" in lines[first]
    assert "PROVISIONAL" in lines[first]
    assert "Alpha Store · 3 ads · 3 creatives" in lines[first + 1]
    assert (
        f"Market demand {score.total}/100 · {score.advertisers} advertisers ({score.saturation})"
        in lines[first + 2]
    )
    assert "Strongest advertiser" not in out
    assert "relevant 5  excluded 1  creatives 5" in out


def test_report_market_factors_and_leader_summary(engine: Engine) -> None:
    sid = _seed(engine)
    leader = _json(sid).leader
    assert leader is not None

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    assert "Market factors" in out
    status = {f.key: f.status.upper() for f in leader.factors}
    assert (
        f"Alpha Store: Longevity {status['longevity']} · Continuity {status['continuity']}"
        f" · Volume {status['volume']} · Angles PENDING"
    ) in out


def test_report_without_leader_shows_total_and_no_leader_text(engine: Engine) -> None:
    sid = _seed(engine, ads=[_ad("1", "Gamma Co", 10, body="Yoga mats on sale")])
    score = _json(sid)
    assert score.leader is None

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    assert f"Winner Score {score.total}/100" in out
    assert "creatives ·" not in out
    assert "Longevity" not in out.split("Market factors")[0]
    assert "Strongest advertiser" not in out
    assert " · Continuity " not in out


def test_report_unknown_snapshot_exit_1(engine: Engine) -> None:
    code, out = _invoke("report", "999")

    assert code == 1
    assert "Snapshot 999 not found. Run: uv run adscout snapshots" in out


def test_report_partial_snapshot_note(engine: Engine) -> None:
    sid = _seed(engine, status="capped")

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    assert "Partial data: this snapshot stopped early (capped)." in out


def test_exclude_then_report_changes_counts(engine: Engine) -> None:
    sid = _seed(engine)
    before = _json(sid)

    code, out = _invoke("exclude", KEYWORD, "--advertiser", "pid-alpha")
    assert code == 0, out
    assert 'Excluded advertiser pid-alpha for "posture corrector".' in out
    after = _json(sid)
    assert (after.relevant_ads, after.excluded_ads) == (
        before.relevant_ads - 3,
        before.excluded_ads + 3,
    )

    code, out = _invoke("exclude", "Posture Correctors", "--ad", "6", "--include")
    assert code == 0, out
    assert 'Included ad 6 for "Posture Correctors".' in out
    assert _json(sid).relevant_ads == after.relevant_ads + 1

    code, out = _invoke("exclude", KEYWORD, "--advertiser", "pid-alpha", "--clear")
    assert code == 0, out
    assert 'Cleared advertiser pid-alpha for "posture corrector".' in out
    assert _json(sid).relevant_ads == before.relevant_ads + 1


def test_exclude_by_snapshot(engine: Engine) -> None:
    sid = _seed(engine)
    before = _json(sid)

    code, out = _invoke("exclude", "--snapshot", str(sid), "--advertiser", "pid-alpha")
    assert code == 0, out
    assert f'Excluded advertiser pid-alpha for snapshot #{sid} ("posture corrector").' in out
    assert _json(sid).relevant_ads == before.relevant_ads - 3


def test_exclude_by_advertiser_snapshot_keys_on_page(engine: Engine) -> None:
    from adscout.db.repo import list_overrides

    with Session(engine) as s:
        q = SearchQuery(kind="advertiser", page_id="555", keyword="★★★")
        snap = create_snapshot(s, q, "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ADS, "status": "ok"}))
        sid = snap.id

    code, out = _invoke("exclude", "--snapshot", str(sid), "--ad", "1")
    assert code == 0, out
    with Session(engine) as s:
        assert [o.target_id for o in list_overrides(s, "page:555")] == ["1"]
    assert _json(sid).excluded_ads == 1


def test_exclude_needs_keyword_or_snapshot(engine: Engine) -> None:
    code, out = _invoke("exclude", "--ad", "1")
    assert code == 2
    assert "give a keyword or --snapshot" in out
    code, out = _invoke("exclude", "--snapshot", "999", "--ad", "1")
    assert code == 1
    assert "Snapshot 999 not found" in out


def test_exclude_requires_one_target(engine: Engine) -> None:
    code, out = _invoke("exclude", KEYWORD)
    assert code == 2
    assert "exactly one of --ad or --advertiser" in out

    code, out = _invoke("exclude", KEYWORD, "--ad", "1", "--advertiser", "pid-alpha")
    assert code == 2
    assert "exactly one of --ad or --advertiser" in out


def test_report_escapes_markup_in_names(engine: Engine) -> None:
    ads = [*ADS, _ad("7", "Shop [/x]", 9), _ad("8", "Shop [/x]", 10)]
    sid = _seed(engine, ads=ads)

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    assert "Shop [/x]" in out


def test_report_shows_emoji_shortcodes_literally(engine: Engine) -> None:
    sid = _seed(engine, ads=[*ADS, _ad("9", "Shop :fire:", 9), _ad("10", "Shop :fire:", 10)])

    code, out = _invoke("report", str(sid))

    assert code == 0, out
    assert "Shop :fire:" in out
    assert "🔥" not in out


def test_report_survives_cp1252_stdout(engine: Engine) -> None:
    """A cp1252 stdout (Windows redirect/legacy console) must not crash on CJK or emoji."""
    ads = [*ADS, _ad("9", "Café 店 Shop 🔥", 9), _ad("10", "Shop :fire:", 10)]
    sid = _seed(engine, ads=ads, status="capped")

    result = CliRunner(charset="cp1252").invoke(app, ["report", str(sid)], env={"COLUMNS": "160"})

    assert result.exception is None, result.output
    assert result.exit_code == 0
    out = result.output
    # A non-UTF pipe gets plain ASCII: whoever reads it (often as UTF-8) sees no U+FFFD.
    assert result.stdout_bytes.isascii()
    assert "PROVISIONAL - angle analysis not run yet" in out
    assert "Partial data: this snapshot stopped early (capped)." in out
    assert "Cafe ? Shop ?" in out
    assert "Shop :fire:" in out
    # Decorative glyphs switch to ASCII rather than "?".
    assert "/100  #" in out
    assert "Pass >=" in out
    assert "% of creatives running >= 90 days" in out
    assert "Alpha Store - 3 ads - 3 creatives" in out
    assert "Market demand " in out
    assert "2026-03 " in out and "?" not in out.split("NEW CREATIVES / MONTH")[1].splitlines()[0]


def test_exclude_warns_unknown_keyword(engine: Engine) -> None:
    _seed(engine)

    code, out = _invoke("exclude", "yoga mat", "--ad", "1")
    assert code == 0, out
    assert 'No saved search uses "yoga mat" yet; the override will apply once you search it.' in out

    code, out = _invoke("exclude", "Posture Correctors", "--ad", "1")
    assert code == 0, out
    assert "No saved search" not in out
