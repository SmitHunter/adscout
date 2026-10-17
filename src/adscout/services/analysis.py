"""Run the angle analysis for a snapshot, reusing earlier results to keep Claude spend low.

Three things stop a needless Claude call: the per-snapshot cache (same input), the
cross-snapshot reuse (a recent analysis of the same search already covers the creatives) and,
for unattended runs only, the daily cap (``AutoAnalysisCap``).
"""

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from adscout.analysis.backends import Analyzer
from adscout.analysis.models import Angle, AngleAnalysis, Hook, Offer, clean
from adscout.analysis.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    AnalysisInputAd,
    build_input,
    build_user_message,
    input_hash,
)
from adscout.db.repo import (
    get_analysis,
    load_ads,
    override_key,
    save_analysis,
    snapshot_kind,
    snapshot_overrides,
)
from adscout.db.tables import Analysis, Snapshot
from adscout.models import Ad
from adscout.scoring import AngleInput, decide_relevance
from adscout.scoring.creatives import creative_key, group_creatives

logger = logging.getLogger(__name__)

NO_RELEVANT = "No relevant ads to analyze."
REUSED_BACKEND = "reused"
REUSE_MIN_COVERAGE = 0.9
ANALYSIS_RUNS_FILE = "analysis_runs.json"


class NoRelevantAds(ValueError):
    def __init__(self) -> None:
        super().__init__(NO_RELEVANT)


class AutoCapReached(Exception):
    """The day's unattended analyses are used up: this one is skipped, Claude isn't called."""


def snapshot_time(snap: Snapshot) -> datetime:
    """The moment the snapshot's data describes; ``days_running`` in the input is fixed to it,
    so the input hash (and the cache) doesn't change from one day to the next."""
    return snap.finished_at or snap.started_at


def analysis_input(session: Session, snapshot_id: int) -> tuple[Snapshot, list[AnalysisInputAd]]:
    """The snapshot and the creatives that would be sent, from the current relevance.

    Raises ``LookupError`` for an unknown snapshot.
    """
    snap = session.get(Snapshot, snapshot_id)
    if snap is None:
        raise LookupError(f"snapshot {snapshot_id} not found")
    ads = load_ads(session, snapshot_id)
    decisions = decide_relevance(
        ads, snap.query, snapshot_overrides(session, snap), kind=snapshot_kind(snap)
    )
    return snap, build_input(ads, decisions, snapshot_time(snap))


def _lead_creatives(
    session: Session, snap: Snapshot, ads: list[Ad], items: list[AnalysisInputAd]
) -> dict[str, frozenset[str]]:
    """Raw creative keys of each sent lead's creative (lead id -> member keys).

    Raw keys (``creative_key`` per ad) stay stable across snapshots even where the grouping
    merges creatives differently, so two snapshots are compared through them.
    """
    decisions = decide_relevance(
        ads, snap.query, snapshot_overrides(session, snap), kind=snapshot_kind(snap)
    )
    relevant = [a for a in ads if (d := decisions.get(a.ad_archive_id)) is not None and d.relevant]
    sent = {i.id for i in items}
    out: dict[str, frozenset[str]] = {}
    for creative in group_creatives(relevant):
        members = frozenset(creative_key(a) for a in creative.ads)
        for ad in creative.ads:
            if ad.ad_archive_id in sent:
                out[ad.ad_archive_id] = members
    return out


def _previous_analysis(
    session: Session, snap: Snapshot, prompt_version: str, model: str
) -> Analysis | None:
    """The most recent real (Claude-made) analysis of another snapshot of the same search
    (keyword or page, and country) with the same prompt version and model.

    Reused rows are never a source: coverage is always measured against the snapshot Claude
    actually saw, so small daily changes can't chain reuse forever and leave stale angles.
    """
    key = override_key(snap)
    if not key:
        return None
    stmt = (
        select(Analysis, Snapshot)
        .join(Snapshot, col(Snapshot.id) == col(Analysis.snapshot_id))
        .where(
            Analysis.prompt_version == prompt_version,
            Analysis.model == model,
            Analysis.backend != REUSED_BACKEND,
            Snapshot.country == snap.country,
            Snapshot.kind == snap.kind,
            col(Snapshot.id) != snap.id,
        )
        .order_by(col(Analysis.created_at).desc(), col(Analysis.id).desc())
    )
    for row, other in session.exec(stmt):
        if override_key(other) == key:
            return row
    return None


def _remap(ids: list[str], mapping: dict[str, list[str]]) -> list[str]:
    return list(dict.fromkeys(new for i in ids for new in mapping.get(i, ())))


def find_reusable_analysis(
    session: Session, snapshot: Snapshot, prompt_version: str, model: str
) -> Analysis | None:
    """A ready-to-save copy of the latest real analysis of the same search, if it still fits.

    It fits when its creatives cover at least 90% of ``snapshot``'s relevant lead creatives
    (compared by raw creative key). The copy's ids are remapped to ``snapshot``'s leads;
    creatives it doesn't cover are simply in no angle, and every new lead matching an old
    lead inherits that lead's angles, hooks and offers. The returned row is not saved:
    ``backend`` is ``"reused"``, ``usage`` is None and ``input_hash`` is the new snapshot's.
    """
    assert snapshot.id is not None
    previous = _previous_analysis(session, snapshot, prompt_version, model)
    if previous is None:
        return None
    old_snap = session.get(Snapshot, previous.snapshot_id)
    if old_snap is None:
        return None
    try:
        result = AngleAnalysis.model_validate(previous.result)
    except ValueError:
        return None
    new_ads = load_ads(session, snapshot.id)
    _, new_items = analysis_input(session, snapshot.id)
    if not new_items:
        return None
    old_ads = load_ads(session, previous.snapshot_id)
    _, old_items = analysis_input(session, previous.snapshot_id)
    # The old leads the stored result speaks for: the ones its input holds.
    old_leads = _lead_creatives(session, old_snap, old_ads, old_items)
    new_leads = _lead_creatives(session, snapshot, new_ads, new_items)
    owner: dict[str, str] = {}  # raw creative key -> old lead id
    for lead, keys in old_leads.items():
        for key in sorted(keys):
            owner.setdefault(key, lead)
    mapping: dict[str, list[str]] = {}  # old lead id -> the new lead ids it covers
    covered = 0
    for item in new_items:
        members = sorted(new_leads.get(item.id, frozenset()))
        old = next((owner[k] for k in members if k in owner), None)
        if old is not None:
            covered += 1
            mapping.setdefault(old, []).append(item.id)
    if covered < REUSE_MIN_COVERAGE * len(new_items):
        return None
    remapped = AngleAnalysis(
        angles=[
            Angle(
                name=a.name,
                description=a.description,
                emotion=a.emotion,
                ad_ids=_remap(a.ad_ids, mapping),
            )
            for a in result.angles
        ],
        hooks=[Hook(text=h.text, ad_ids=_remap(h.ad_ids, mapping)) for h in result.hooks],
        offers=[
            Offer(kind=o.kind, text=o.text, ad_ids=_remap(o.ad_ids, mapping)) for o in result.offers
        ],
        not_product_ad_ids=_remap(result.not_product_ad_ids, mapping),
        seasonality=result.seasonality,
        seasonality_note=result.seasonality_note,
        verdict=result.verdict,
    )
    cleaned = clean(remapped, {i.id for i in new_items})
    return Analysis(
        snapshot_id=snapshot.id,
        prompt_version=prompt_version,
        model=model,
        backend=REUSED_BACKEND,
        input_hash=input_hash(new_items),
        ad_count=len(new_items),
        result=cleaned.model_dump(mode="json"),
        usage=None,
        created_at=previous.created_at,
    )


_CACHE_USAGE_KEYS = ("cache_creation_input_tokens", "cache_read_input_tokens")


def _usage(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Token counts only: the CLI and API usage shapes differ beyond these keys.

    Input and output are always stored (None when missing); the prompt-cache counts only
    when present.
    """

    def count(key: str) -> int | None:
        value = (raw or {}).get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    out: dict[str, Any] = {
        "input_tokens": count("input_tokens"),
        "output_tokens": count("output_tokens"),
    }
    for key in _CACHE_USAGE_KEYS:
        if (value := count(key)) is not None:
            out[key] = value
    return out


async def run_analysis(
    engine: Engine,
    snapshot_id: int,
    analyzer: Analyzer,
    now: datetime,
    force: bool = False,
    *,
    allow_call: Callable[[], bool] | None = None,
) -> Analysis:
    """Analyse the snapshot's relevant creatives, or reuse an earlier result unless ``force``.

    Reuse is the snapshot's own cached result, else a recent analysis of the same search
    that still covers its creatives (stored as a new ``"reused"`` row). Only then is Claude
    called, and only if ``allow_call`` (the daily cap of unattended runs) agrees.

    The input is built at the snapshot's own time (see :func:`snapshot_time`), not ``now``,
    so re-running on a later day hits the cache. Raises ``LookupError`` (unknown snapshot),
    :class:`NoRelevantAds` (a ``ValueError``), :class:`AutoCapReached` or ``AnalysisError``
    from the analyzer.
    """
    del now  # Part of the call contract; the input deliberately ignores the wall clock.
    with Session(engine) as session:
        snap, items = analysis_input(session, snapshot_id)
        if not items:
            raise NoRelevantAds
        digest = input_hash(items)
        if not force:
            cached = get_analysis(session, snapshot_id, PROMPT_VERSION, analyzer.model, digest)
            if cached is not None:
                return cached
            reused = find_reusable_analysis(session, snap, PROMPT_VERSION, analyzer.model)
            if reused is not None:
                logger.info("Reused an earlier analysis for snapshot %s", snapshot_id)
                return save_analysis(
                    session,
                    snapshot_id=snapshot_id,
                    prompt_version=PROMPT_VERSION,
                    model=analyzer.model,
                    backend=REUSED_BACKEND,
                    input_hash=digest,
                    ad_count=len(items),
                    result=reused.result,
                    usage=None,
                )
        user = build_user_message(snap.query, snap.country, items)
    if allow_call is not None and not allow_call():
        raise AutoCapReached

    # No session is held open while Claude works (this can take minutes).
    result, usage = await analyzer.analyze(SYSTEM_PROMPT, user)
    cleaned = clean(result, {i.id for i in items})

    with Session(engine) as session:
        return save_analysis(
            session,
            snapshot_id=snapshot_id,
            prompt_version=PROMPT_VERSION,
            model=analyzer.model,
            backend=analyzer.name,
            input_hash=digest,
            ad_count=len(items),
            result=cleaned.model_dump(mode="json"),
            usage=_usage(usage),
        )


def angle_input_from(result: AngleAnalysis) -> AngleInput:
    return AngleInput(
        angles=[(a.name, a.ad_ids) for a in result.angles],
        not_product=list(result.not_product_ad_ids),
    )


def angle_input_for(analysis: Analysis) -> AngleInput:
    return angle_input_from(AngleAnalysis.model_validate(analysis.result))


class AutoAnalysisCap:
    """The daily budget of unattended (auto-analyze and watch-run) Claude analyses.

    The count lives in ``<data_dir>/analysis_runs.json`` as ``{"date", "count"}`` for the
    local day; a missing, corrupt or older file reads as zero. Manual runs never touch it.
    """

    def __init__(self, data_dir: Path, limit: Callable[[], int], today: Callable[[], date]) -> None:
        self._path = data_dir / ANALYSIS_RUNS_FILE
        self._limit = limit
        self._today = today
        self._lock = threading.Lock()

    def used_today(self) -> int:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(raw, dict) or raw.get("date") != self._today().isoformat():
            return 0
        count = raw.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
        return 0

    def exhausted(self) -> bool:
        return self.used_today() >= self._limit()

    def take(self) -> bool:
        """Count one analysis if the day's budget allows it; False when it is used up."""
        with self._lock:
            used = self.used_today()
            if used >= self._limit():
                return False
            self._write(used + 1)
            return True

    def _write(self, count: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        payload = {"date": self._today().isoformat(), "count": count}
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self._path)
