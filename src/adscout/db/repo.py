from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlmodel import Session, SQLModel, col, create_engine, select
from sqlmodel.sql.expression import SelectOfScalar

from adscout.db.tables import AdRow, Alert, Analysis, Exclusion, Snapshot, WatchItem
from adscout.models import Ad, MediaItem, SearchKind, SearchQuery
from adscout.scoring.relevance import (
    Override,
    normalise_keyword,
    override_key_norm,
    page_override_key,
    require_keyword,
)
from adscout.sources import SearchResult


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """create_all never alters existing tables; add columns introduced after M2."""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(exclusion)"))}
        if "keyword_raw" not in cols:
            conn.execute(
                text("ALTER TABLE exclusion ADD COLUMN keyword_raw TEXT NOT NULL DEFAULT ''")
            )
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(snapshot)"))}
        if "watch_item_id" not in cols:
            conn.execute(text("ALTER TABLE snapshot ADD COLUMN watch_item_id INTEGER"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_snapshot_watch_item_id ON snapshot (watch_item_id)")
        )
        if "page_id" not in cols:
            conn.execute(text("ALTER TABLE snapshot ADD COLUMN page_id TEXT"))


def _get(session: Session, snapshot_id: int) -> Snapshot:
    snap = session.get(Snapshot, snapshot_id)
    if snap is None:
        raise LookupError(f"snapshot {snapshot_id} not found")
    return snap


def create_snapshot(
    session: Session,
    q: SearchQuery,
    source: str,
    *,
    status: str = "running",
    watch_item_id: int | None = None,
) -> Snapshot:
    """A new snapshot row: "running", or "queued" behind another search."""
    snap = Snapshot(
        query=q.keyword,
        kind=q.kind,
        page_id=q.page_id,
        country=q.country,
        active_status=q.active_status,
        media_type=q.media_type,
        source=source,
        started_at=datetime.now(UTC),
        status=status,
        watch_item_id=watch_item_id,
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap


def mark_running(session: Session, snapshot_id: int) -> None:
    """A queued snapshot starts now: "running", with its start time reset."""
    snap = _get(session, snapshot_id)
    snap.status = "running"
    snap.started_at = datetime.now(UTC)
    session.add(snap)
    session.commit()


def finish_snapshot(session: Session, snapshot_id: int, result: SearchResult) -> Snapshot:
    snap = _get(session, snapshot_id)
    ads = list({ad.ad_archive_id: ad for ad in reversed(result.ads)}.values())[::-1]
    try:
        for ad in ads:
            session.add(
                AdRow(
                    **ad.model_dump(exclude={"media"}),
                    media=[m.model_dump() for m in ad.media],
                    snapshot_id=snapshot_id,
                )
            )
        snap.status = result.status
        snap.ad_count = len(ads)
        snap.advertiser_count = len({k for a in ads if (k := a.advertiser_key)})
        snap.finished_at = datetime.now(UTC)
        snap.error_message = result.message
        session.add(snap)
        session.commit()
    except StatementError as exc:
        session.rollback()
        # SQLAlchemy wraps TypeDecorator errors; surface the original ValueError.
        if isinstance(exc.orig, ValueError):
            raise exc.orig from exc
        raise
    except Exception:
        session.rollback()
        raise
    session.refresh(snap)
    return snap


def fail_snapshot(
    session: Session, snapshot_id: int, message: str, *, status: str = "error"
) -> Snapshot:
    """Close a snapshot without ads: ``status`` is "error" (default) or "cancelled"."""
    snap = _get(session, snapshot_id)
    snap.status = status
    snap.error_message = message
    snap.finished_at = datetime.now(UTC)
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap


INTERRUPTED_MESSAGE = "Interrupted: AdScout was closed during this search."


def sweep_running(session: Session, statuses: tuple[str, ...] = ("running", "queued")) -> int:
    """Mark snapshots left in ``statuses`` ("running"/"queued") by a previous process as
    "error"; return the count."""
    stale = list(session.exec(select(Snapshot).where(col(Snapshot.status).in_(statuses))))
    now = datetime.now(UTC)
    for snap in stale:
        snap.status = "error"
        snap.error_message = INTERRUPTED_MESSAGE
        snap.finished_at = now
        session.add(snap)
    session.commit()
    return len(stale)


def list_snapshots(session: Session, keyword: str | None = None) -> list[Snapshot]:
    stmt = select(Snapshot).order_by(col(Snapshot.started_at).desc(), col(Snapshot.id).desc())
    if keyword is not None:
        stmt = stmt.where(Snapshot.query == keyword)
    return list(session.exec(stmt))


def load_ads(session: Session, snapshot_id: int) -> list[Ad]:
    stmt = (
        select(AdRow)
        .where(AdRow.snapshot_id == snapshot_id)
        .order_by(col(AdRow.start_date), col(AdRow.ad_archive_id))
    )
    fields = set(Ad.model_fields)
    return [Ad.model_validate(row.model_dump(include=fields)) for row in session.exec(stmt)]


def update_ad_media(
    session: Session, snapshot_id: int, ad_archive_id: str, media: list[MediaItem]
) -> None:
    """Replace one ad row's media JSON (a new list, so the change is detected)."""
    row = session.exec(
        select(AdRow).where(AdRow.snapshot_id == snapshot_id, AdRow.ad_archive_id == ad_archive_id)
    ).first()
    if row is None:
        return
    row.media = [m.model_dump() for m in media]
    session.add(row)
    session.commit()


def _override_stmt(keyword: str, scope: str, target_id: str) -> SelectOfScalar[Exclusion]:
    return select(Exclusion).where(
        Exclusion.keyword_norm == override_key_norm(keyword),
        Exclusion.scope == scope,
        Exclusion.target_id == target_id,
    )


def _find_override(session: Session, keyword: str, scope: str, target_id: str) -> Exclusion | None:
    return session.exec(_override_stmt(keyword, scope, target_id)).first()


def snapshot_keywords(session: Session) -> set[str]:
    """Normalised keywords of all saved searches."""
    out: set[str] = set()
    for query in session.exec(select(Snapshot.query).distinct()):
        try:
            out.add(require_keyword(query))
        except ValueError:
            continue
    return out


def set_override(
    session: Session,
    keyword: str,
    scope: Literal["ad", "advertiser"],
    target_id: str,
    mode: Literal["exclude", "include"],
) -> None:
    """Store an override under ``keyword``: a keyword (normalised) or a ``page:<id>`` key
    (see ``override_key``)."""
    row = _find_override(session, keyword, scope, target_id)
    if row is None:
        row = Exclusion(
            keyword_norm=override_key_norm(keyword),
            keyword_raw=keyword.strip(),
            scope=scope,
            target_id=target_id,
            mode=mode,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        try:
            session.commit()
            return
        except IntegrityError:
            session.rollback()
            row = _find_override(session, keyword, scope, target_id)
            if row is None:
                raise
    row.mode = mode
    session.add(row)
    session.commit()


def clear_override(
    session: Session, keyword: str, scope: Literal["ad", "advertiser"], target_id: str
) -> bool:
    row = session.exec(_override_stmt(keyword, scope, target_id)).first()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_overrides(session: Session, keyword: str) -> list[Override]:
    stmt = (
        select(Exclusion)
        .where(Exclusion.keyword_norm == override_key_norm(keyword))
        .order_by(col(Exclusion.id))
    )
    return [
        Override.model_validate(r.model_dump(include={"scope", "target_id", "mode"}))
        for r in session.exec(stmt)
    ]


def snapshot_kind(snap: Snapshot) -> SearchKind:
    """The snapshot's search kind; anything unrecognised reads as a keyword search."""
    return "advertiser" if snap.kind == "advertiser" else "keyword"


def override_key(snap: Snapshot) -> str:
    """The key the snapshot's overrides are stored under: ``page:<page_id>`` for an
    advertiser search (never its display name), else the normalised keyword."""
    if snapshot_kind(snap) == "advertiser" and snap.page_id:
        return page_override_key(snap.page_id)
    return normalise_keyword(snap.query)


def snapshot_overrides(session: Session, snap: Snapshot) -> list[Override]:
    """The user's overrides for the snapshot (none when its keyword has no words)."""
    key = override_key(snap)
    return list_overrides(session, key) if key else []


def _analysis_stmt(
    snapshot_id: int, prompt_version: str, model: str, input_hash: str
) -> SelectOfScalar[Analysis]:
    return select(Analysis).where(
        Analysis.snapshot_id == snapshot_id,
        Analysis.prompt_version == prompt_version,
        Analysis.model == model,
        Analysis.input_hash == input_hash,
    )


def get_analysis(
    session: Session, snapshot_id: int, prompt_version: str, model: str, input_hash: str
) -> Analysis | None:
    return session.exec(_analysis_stmt(snapshot_id, prompt_version, model, input_hash)).first()


def latest_analysis(session: Session, snapshot_id: int) -> Analysis | None:
    stmt = (
        select(Analysis)
        .where(Analysis.snapshot_id == snapshot_id)
        .order_by(col(Analysis.created_at).desc(), col(Analysis.id).desc())
    )
    return session.exec(stmt).first()


def save_analysis(
    session: Session,
    *,
    snapshot_id: int,
    prompt_version: str,
    model: str,
    backend: str,
    input_hash: str,
    ad_count: int,
    result: dict[str, Any],
    usage: dict[str, Any] | None = None,
) -> Analysis:
    """Insert, or replace the cached row with the same (snapshot, prompt, model, hash)."""

    def fill(row: Analysis) -> Analysis:
        row.backend = backend
        row.ad_count = ad_count
        row.result = result
        row.usage = usage
        row.created_at = datetime.now(UTC)
        session.add(row)
        return row

    existing = get_analysis(session, snapshot_id, prompt_version, model, input_hash)
    row = fill(
        existing
        or Analysis(
            snapshot_id=snapshot_id,
            prompt_version=prompt_version,
            model=model,
            input_hash=input_hash,
            backend=backend,
            ad_count=ad_count,
            created_at=datetime.now(UTC),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raced = get_analysis(session, snapshot_id, prompt_version, model, input_hash)
        if raced is None:
            raise
        row = fill(raced)
        session.commit()
    session.refresh(row)
    return row


# --- Watchlist (M5) ---------------------------------------------------------------------


def _watch_stmt(q: SearchQuery) -> SelectOfScalar[WatchItem]:
    """The watch item that re-runs ``q``: same page (advertiser) or keyword, and filters."""
    stmt = select(WatchItem).where(
        WatchItem.kind == q.kind,
        WatchItem.country == q.country,
        WatchItem.active_status == q.active_status,
        WatchItem.media_type == q.media_type,
    )
    if q.kind == "advertiser":
        return stmt.where(WatchItem.page_id == q.page_id)
    return stmt.where(WatchItem.query == q.keyword)


def find_watch_item(session: Session, q: SearchQuery) -> WatchItem | None:
    return session.exec(_watch_stmt(q).order_by(col(WatchItem.id))).first()


def create_watch_item(session: Session, q: SearchQuery) -> WatchItem:
    """Watch ``q`` daily; returns the existing item when the same search is already watched."""
    existing = find_watch_item(session, q)
    if existing is not None:
        return existing
    item = WatchItem(
        kind=q.kind,
        query=q.keyword,
        page_id=q.page_id,
        country=q.country,
        active_status=q.active_status,
        media_type=q.media_type,
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def get_watch_item(session: Session, item_id: int) -> WatchItem | None:
    return session.get(WatchItem, item_id)


def list_watch_items(session: Session, *, enabled_only: bool = False) -> list[WatchItem]:
    """Watch items, oldest first."""
    stmt = select(WatchItem).order_by(col(WatchItem.created_at), col(WatchItem.id))
    if enabled_only:
        stmt = stmt.where(col(WatchItem.enabled).is_(True))
    return list(session.exec(stmt))


def update_watch_item(
    session: Session,
    item_id: int,
    *,
    enabled: bool | None = None,
    last_run_at: datetime | None = None,
    last_status: str | None = None,
) -> WatchItem:
    """Set the given fields (None leaves a field unchanged); raises ``LookupError``."""
    item = session.get(WatchItem, item_id)
    if item is None:
        raise LookupError(f"watch item {item_id} not found")
    if enabled is not None:
        item.enabled = enabled
    if last_run_at is not None:
        item.last_run_at = last_run_at
    if last_status is not None:
        item.last_status = last_status
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def delete_watch_item(session: Session, item_id: int) -> bool:
    """Delete the item and its alerts; its snapshots stay, as ordinary saved searches."""
    item = session.get(WatchItem, item_id)
    if item is None:
        return False
    for alert in session.exec(select(Alert).where(Alert.watch_item_id == item_id)):
        session.delete(alert)
    for snap in session.exec(select(Snapshot).where(Snapshot.watch_item_id == item_id)):
        snap.watch_item_id = None
        session.add(snap)
    session.delete(item)
    session.commit()
    return True


def snapshots_for_watch(session: Session, item_id: int) -> list[Snapshot]:
    """The item's snapshots, oldest first."""
    stmt = (
        select(Snapshot)
        .where(Snapshot.watch_item_id == item_id)
        .order_by(col(Snapshot.started_at), col(Snapshot.id))
    )
    return list(session.exec(stmt))


def add_alert(
    session: Session,
    *,
    watch_item_id: int,
    snapshot_id: int | None,
    kind: Literal["change", "blocked"],
    message: str,
    change_pct: float | None = None,
) -> Alert:
    alert = Alert(
        watch_item_id=watch_item_id,
        snapshot_id=snapshot_id,
        kind=kind,
        change_pct=change_pct,
        message=message,
        created_at=datetime.now(UTC),
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


def list_alerts(
    session: Session, *, unread_only: bool = False, limit: int | None = None
) -> list[Alert]:
    """Alerts, newest first (the newest ``limit`` when given)."""
    stmt = select(Alert).order_by(col(Alert.created_at).desc(), col(Alert.id).desc())
    if unread_only:
        stmt = stmt.where(col(Alert.read).is_(False))
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.exec(stmt))


def mark_read(session: Session, ids: Collection[int] | Literal["all"]) -> int:
    """Mark the given alerts (or every alert) read; returns how many were unread."""
    stmt = select(Alert).where(col(Alert.read).is_(False))
    if ids != "all":
        stmt = stmt.where(col(Alert.id).in_(list(ids)))
    rows = list(session.exec(stmt))
    for row in rows:
        row.read = True
        session.add(row)
    session.commit()
    return len(rows)
