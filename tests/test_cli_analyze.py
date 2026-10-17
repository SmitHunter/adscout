import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session
from typer.testing import CliRunner

import adscout.analysis.backends as backends
import adscout.cli as cli
from adscout.analysis.models import AngleAnalysis
from adscout.cli import app
from adscout.config import Settings
from adscout.db.repo import create_snapshot, finish_snapshot, make_engine
from adscout.models import Ad, SearchQuery
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

RESULT: dict[str, Any] = {
    "angles": [
        {
            "name": "Back pain [bold]relief[/bold]",
            "description": "Relief from desk-job back pain.",
            "emotion": "pain_relief",
            "ad_ids": ["1", "2", "3"],
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
    "offers": [{"kind": "free_shipping", "text": "Free shipping :fire:", "ad_ids": ["2"]}],
    "not_product_ad_ids": [],
    "seasonality": "evergreen",
    "seasonality_note": "No seasonal cues.",
    "verdict": "Three angles are in play. Long runners favour pain relief. Risk is copycats.",
}


class FakeAnalyzer:
    name: backends.BackendName = "cli"

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls = 0

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        self.calls += 1
        return AngleAnalysis.model_validate(RESULT), None


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = make_engine(tmp_path / "adscout.db")
    monkeypatch.setattr(cli, "get_engine", lambda: eng)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    settings = Settings(_env_file=None, data_dir=tmp_path, anthropic_api_key=None)
    monkeypatch.setattr(cli, "effective_settings", lambda: settings)
    return eng


def _seed(engine: Engine, ads: list[Ad] = ADS) -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=KEYWORD, country="US"), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult.model_validate({"ads": ads, "status": "ok"}))
        return snap.id


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "160"})
    return result.exit_code, result.output


def _fake(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, str | None]]:
    seen: list[tuple[str | None, str | None]] = []
    made: list[FakeAnalyzer] = []

    def resolve(
        settings: Settings, which: str | None = None, model: str | None = None
    ) -> FakeAnalyzer:
        seen.append((which, model))
        made.append(FakeAnalyzer(model or settings.analysis_model))
        return made[-1]

    monkeypatch.setattr(cli, "resolve_analyzer", resolve)
    return seen


def test_cli_analyze_prints_verdict(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine)
    seen = _fake(monkeypatch)
    code, out = _invoke("analyze", str(sid))
    assert code == 0, out
    assert seen == [(None, None)]
    assert "Three angles are in play." in out
    assert "Back pain [bold]relief[/bold]" in out  # escaped, printed literally
    assert "pain relief" in out  # emotion, readable
    assert "Confidence" in out and "Gift" in out
    assert "Free shipping :fire:" in out  # offers, emoji codes left alone
    assert "free shipping" in out
    assert "Creatives" in out

    # Report now uses the analysis: no provisional badge, real angles row.
    code, out = _invoke("report", str(sid))
    assert code == 0, out
    assert "PROVISIONAL" not in out
    assert "3 angles" in out


def test_cli_analyze_passes_backend_and_model(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _seed(engine)
    seen = _fake(monkeypatch)
    code, out = _invoke(
        "analyze", str(sid), "--backend", "api", "--model", "claude-opus-5-5", "--force"
    )
    assert code == 0, out
    assert seen == [("api", "claude-opus-5-5")]


def test_cli_analyze_rejects_unknown_model(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine)
    _fake(monkeypatch)
    code, _out = _invoke("analyze", str(sid), "--model", "gpt-4")
    assert code != 0


def test_cli_analyze_cached_note(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine)
    _fake(monkeypatch)
    assert _invoke("analyze", str(sid))[0] == 0
    code, out = _invoke("analyze", str(sid))
    assert code == 0, out
    assert "--force" in out


def test_cli_analyze_unavailable_exit_1(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    code, out = _invoke("analyze", str(sid))
    assert code == 1
    assert " ".join(out.split()) == backends.UNAVAILABLE


def test_cli_analyze_explicit_api_without_key(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _seed(engine)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    code, out = _invoke("analyze", str(sid), "--backend", "api")
    assert code == 1
    assert " ".join(out.split()) == backends.NO_API_KEY


def test_cli_analyze_unknown_snapshot(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch)
    code, out = _invoke("analyze", "999")
    assert code == 1
    assert "Snapshot 999 not found" in out


def test_cli_analyze_zero_relevant(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine, [_ad("6", "Gamma Co", 10, body="Yoga mats on sale")])
    _fake(monkeypatch)
    code, out = _invoke("analyze", str(sid))
    assert code == 1
    assert "No relevant ads to analyze." in out


def test_cli_analyze_error_is_shown(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine)

    class Failing(FakeAnalyzer):
        async def analyze(
            self, system: str, user: str
        ) -> tuple[AngleAnalysis, dict[str, Any] | None]:
            raise backends.AnalysisError("Claude Code failed: [boom]")

    monkeypatch.setattr(cli, "resolve_analyzer", lambda s, which=None, model=None: Failing("m"))
    code, out = _invoke("analyze", str(sid))
    assert code == 1
    assert "Claude Code failed: [boom]" in out


def test_cli_analyze_says_what_it_sends(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _seed(engine)
    _fake(monkeypatch)
    code, out = _invoke("analyze", str(sid))
    assert code == 0, out
    assert "Sending 5 creatives to Claude Code (claude-sonnet-5-5)." in out
    assert "Cached analysis" not in out
    code, out = _invoke("analyze", str(sid))
    assert code == 0, out
    assert "Sending" not in out
    assert "Cached analysis from" in out


def test_cli_analyze_and_report_print_off_topic_warning(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _seed(engine)
    monkeypatch.setitem(RESULT, "not_product_ad_ids", ["4", "5"])
    _fake(monkeypatch)
    warning = (
        "Claude marked 2 ads as not selling this product. They're excluded from the score. "
        "Review them in the Excluded tab."
    )
    code, out = _invoke("analyze", str(sid))
    assert code == 0, out
    assert warning in " ".join(out.split())
    code, out = _invoke("report", str(sid))
    assert code == 0, out
    assert warning in " ".join(out.split())
