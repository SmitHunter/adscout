"""Claude cost safety: cross-snapshot reuse, the daily auto cap and job hygiene."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from adscout.analysis.backends import BackendName
from adscout.analysis.models import AngleAnalysis
from adscout.analysis.prompt import PROMPT_VERSION
from adscout.db.repo import create_snapshot, finish_snapshot, latest_analysis, make_engine
from adscout.db.tables import Snapshot
from adscout.models import Ad, MediaItem, SearchQuery
from adscout.services.analysis import (
    AutoAnalysisCap,
    find_reusable_analysis,
    run_analysis,
)
from adscout.services.analysis_jobs import AnalysisJob, AnalysisManager
from adscout.sources import SearchResult

NOW = datetime(2026, 10, 15, tzinfo=UTC)
KEYWORD = "posture corrector"


def _ad(ad_id: str, n: int, product: str = KEYWORD) -> Ad:
    return Ad(
        source="fake",
        ad_archive_id=ad_id,
        page_id=f"pid-{n % 3}",
        page_name=f"Shop {n % 3}",
        start_date=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=n),
        is_active=True,
        body=f"Best {product}, variant {n}",
    )


def _ads(prefix: str, variants: range, product: str = KEYWORD) -> list[Ad]:
    return [_ad(f"{prefix}{n}", n, product) for n in variants]


class FakeAnalyzer:
    def __init__(self, model: str = "claude-sonnet-5-5") -> None:
        self.name: BackendName = "cli"
        self.model = model
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        ids = [i["id"] for i in json.loads(user.split("<ads>\n", 1)[1].split("\n</ads>")[0])]
        result = {
            "angles": [
                {
                    "name": "Back pain relief",
                    "description": "Relief.",
                    "emotion": "pain_relief",
                    "ad_ids": ids,
                }
            ],
            "hooks": [{"text": "Still sore?", "ad_ids": ids[:1]}],
            "offers": [],
            "not_product_ad_ids": [],
            "seasonality": "evergreen",
            "seasonality_note": "None.",
            "verdict": "One. Two. Three.",
        }
        return AngleAnalysis.model_validate(result), {"input_tokens": 1, "output_tokens": 1}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "adscout.db")


def _seed(engine: Engine, ads: list[Ad], keyword: str = KEYWORD, country: str = "US") -> int:
    with Session(engine) as s:
        snap = create_snapshot(s, SearchQuery(keyword=keyword, country=country), "fake")
        assert snap.id is not None
        finish_snapshot(s, snap.id, SearchResult(ads=ads, status="ok"))
        return snap.id


async def test_reuses_previous_analysis_when_creatives_overlap_90pct(engine: Engine) -> None:
    fake = FakeAnalyzer()
    first = _seed(engine, _ads("a", range(10)))
    await run_analysis(engine, first, fake, NOW)
    assert fake.calls == 1

    # Next day: the same 10 creatives (new archive ids), plus one new one: 10/11 >= 90%.
    second = _seed(engine, [*_ads("b", range(10)), _ad("b99", 99)])
    with Session(engine) as s:
        snap = s.get(Snapshot, second)
        assert snap is not None
        found = find_reusable_analysis(s, snap, PROMPT_VERSION, fake.model)
    assert found is not None

    row = await run_analysis(engine, second, fake, NOW)
    assert fake.calls == 1  # Claude was not called again
    assert row.snapshot_id == second
    assert row.backend == "reused" and row.usage is None
    assert row.model == fake.model and row.prompt_version == PROMPT_VERSION
    result = AngleAnalysis.model_validate(row.result)
    ids = set(result.angles[0].ad_ids)
    # Remapped onto the new snapshot's leads; the uncovered creative is in no angle.
    assert ids == {f"b{n}" for n in range(10)}
    assert result.hooks[0].ad_ids[0].startswith("b")
    # Stored as the new snapshot's analysis; the old one is untouched.
    with Session(engine) as s:
        latest = latest_analysis(s, first)
        assert latest is not None and latest.backend == "cli"

    # Re-running hits the exact cache of the reused row.
    again = await run_analysis(engine, second, fake, NOW)
    assert again.id == row.id and fake.calls == 1


async def test_no_reuse_below_overlap_or_force(engine: Engine) -> None:
    fake = FakeAnalyzer()
    first = _seed(engine, _ads("a", range(10)))
    await run_analysis(engine, first, fake, NOW)

    # 8 of 10 creatives seen before: 80% < 90%.
    low = _seed(engine, [*_ads("b", range(8)), _ad("b50", 50), _ad("b51", 51)])
    row = await run_analysis(engine, low, fake, NOW)
    assert fake.calls == 2 and row.backend == "cli"

    # Full overlap, but force skips reuse.
    full = _seed(engine, _ads("c", range(10)))
    forced = await run_analysis(engine, full, fake, NOW, force=True)
    assert fake.calls == 3 and forced.backend == "cli"

    # Another country, model or keyword never reuses.
    other_country = _seed(engine, _ads("d", range(10)), country="GB")
    with Session(engine) as s:
        snap = s.get(Snapshot, other_country)
        assert snap is not None
        assert find_reusable_analysis(s, snap, PROMPT_VERSION, fake.model) is None
        snap_full = s.get(Snapshot, full)
        assert snap_full is not None
        assert find_reusable_analysis(s, snap_full, PROMPT_VERSION, "claude-opus-5-5") is None
        assert find_reusable_analysis(s, snap_full, "angles-v0", fake.model) is None


async def test_reuse_measured_against_last_real_analysis_not_chained(engine: Engine) -> None:
    # Each day swaps one of ten creatives (10% change per day).
    fake = FakeAnalyzer()
    day0 = _seed(engine, _ads("a", range(0, 10)))
    await run_analysis(engine, day0, fake, NOW)
    assert fake.calls == 1

    # Day 1 vs the real source (day 0): 9/10 = 90% -> reused.
    day1 = _seed(engine, _ads("b", range(1, 11)))
    row1 = await run_analysis(engine, day1, fake, NOW)
    assert row1.backend == "reused" and fake.calls == 1

    # Day 2 vs day 1 (a reused row) would be 90% again, but vs the real source (day 0)
    # it is 8/10 = 80%: Claude is called.
    day2 = _seed(engine, _ads("c", range(2, 12)))
    row2 = await run_analysis(engine, day2, fake, NOW)
    assert row2.backend == "cli" and fake.calls == 2

    # Day 3 measures against day 2, the new real analysis: 9/10 -> reused again.
    day3 = _seed(engine, _ads("d", range(3, 13)))
    with Session(engine) as s:
        snap = s.get(Snapshot, day3)
        assert snap is not None
        found = find_reusable_analysis(s, snap, PROMPT_VERSION, fake.model)
    assert found is not None
    row3 = await run_analysis(engine, day3, fake, NOW)
    assert row3.backend == "reused" and fake.calls == 2
    ids = set(AngleAnalysis.model_validate(row3.result).angles[0].ad_ids)
    assert ids == {f"d{n}" for n in range(3, 12)}  # remapped from day 2's leads


def _media_ad(ad_id: str, n: int, body: str, phash: str) -> Ad:
    ad = _ad(ad_id, n)
    return ad.model_copy(
        update={
            "body": body,
            "media": [
                MediaItem(type="image", url="https://cdn.example/img/shared.jpg", phash=phash)
            ],
        }
    )


async def test_every_new_lead_inherits_its_old_leads_angles(engine: Engine) -> None:
    fake = FakeAnalyzer()
    same = "ffff0000ffff0000"
    # Day 0: two copies share an image, so they are merged into one creative (one lead).
    day0 = _seed(
        engine,
        [
            _media_ad("a1", 1, "Best posture corrector for desk workers", same),
            _media_ad("a2", 2, "Best posture corrector for runners", same),
        ],
    )
    await run_analysis(engine, day0, fake, NOW)
    # Day 1: the same two copies, now with images far apart: two creatives, two leads, and
    # both are covered by day 0's single lead.
    day1 = _seed(
        engine,
        [
            _media_ad("b1", 1, "Best posture corrector for desk workers", "0000000000000000"),
            _media_ad("b2", 2, "Best posture corrector for runners", "ffffffffffffffff"),
        ],
    )
    row = await run_analysis(engine, day1, fake, NOW)
    assert row.backend == "reused" and fake.calls == 1
    result = AngleAnalysis.model_validate(row.result)
    assert set(result.angles[0].ad_ids) == {"b1", "b2"}


def _manager(
    engine: Engine,
    analyzer: FakeAnalyzer,
    cap: AutoAnalysisCap | None = None,
    now: list[datetime] | None = None,
) -> AnalysisManager:
    clock = now if now is not None else [NOW]
    return AnalysisManager(
        engine, lambda: analyzer, auto_enabled=lambda: True, now=lambda: clock[0], cap=cap
    )


async def _finish(manager: AnalysisManager, sid: int) -> AnalysisJob | None:
    for _ in range(300):
        job = manager.status(sid)
        if job is None or job.state != "running":
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("analysis did not finish")


async def test_daily_auto_cap_skips_and_manual_not_capped(engine: Engine, tmp_path: Path) -> None:
    fake = FakeAnalyzer()
    day = [date(2026, 10, 15)]
    cap = AutoAnalysisCap(tmp_path / "data", lambda: 2, lambda: day[0])
    manager = _manager(engine, fake, cap)
    # Different keywords, so nothing is reused.
    products = ["posture corrector", "neck pillow", "ice roller"]
    sids = [_seed(engine, _ads(p[0], range(3), p), keyword=p) for p in products]
    for sid in sids:
        await manager.start_after_search(sid)
        await _finish(manager, sid)
    assert fake.calls == 2
    assert manager.status(sids[2]) is None  # skipped, no job
    runs = json.loads((tmp_path / "data" / "analysis_runs.json").read_text(encoding="utf-8"))
    assert runs == {"date": "2026-10-15", "count": 2}

    # Manual runs are never capped (and don't count).
    await manager.start(sids[2], force=False)
    done = await _finish(manager, sids[2])
    assert done is not None and done.state == "done" and fake.calls == 3
    assert cap.used_today() == 2

    # A reused analysis costs nothing and doesn't count either.
    reused = _seed(engine, _ads("w", range(3)))
    await manager.start_after_search(reused)  # the cap is reached: skipped before reuse
    assert manager.status(reused) is None
    day[0] = date(2026, 10, 16)
    await manager.start_after_search(reused)
    await _finish(manager, reused)
    assert fake.calls == 3 and cap.used_today() == 0

    # A new day starts a new count; a corrupt file reads as zero.
    (tmp_path / "data" / "analysis_runs.json").write_text("{nope", encoding="utf-8")
    assert cap.used_today() == 0


async def test_jobs_pruned_after_an_hour(engine: Engine) -> None:
    fake = FakeAnalyzer()
    clock = [NOW]
    manager = _manager(engine, fake, now=clock)
    old = _seed(engine, _ads("a", range(3)))
    await manager.start(old, force=False)
    await _finish(manager, old)
    assert old in manager._jobs

    clock[0] = NOW + timedelta(minutes=30)
    other = _seed(engine, _ads("b", range(3), "neck pillow"), keyword="neck pillow")
    await manager.start(other, force=False)
    assert old in manager._jobs  # finished only 30 minutes ago

    clock[0] = NOW + timedelta(minutes=61)
    fake.gate = asyncio.Event()
    third = _seed(engine, _ads("c", range(3), "ice roller"), keyword="ice roller")
    await manager.start(third, force=False)
    assert old not in manager._jobs
    assert third in manager._jobs  # running jobs are never pruned
    fake.gate.set()
    await _finish(manager, third)
    # The pruned job's analysis is still reported from the DB.
    job = manager.status(old)
    assert job is not None and job.state == "done"
