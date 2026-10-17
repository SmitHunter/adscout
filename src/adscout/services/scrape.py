"""Run one search against a Source and persist it as a Snapshot."""

import asyncio
import logging

from sqlalchemy import Engine
from sqlmodel import Session

from adscout.db.repo import create_snapshot, fail_snapshot, finish_snapshot
from adscout.db.tables import Snapshot
from adscout.models import SearchQuery
from adscout.sources import (
    CancelToken,
    ProgressCallback,
    SearchResult,
    Source,
    SourceBlocked,
    SourceError,
)

logger = logging.getLogger(__name__)


def _close_quietly(engine: Engine, snapshot_id: int, message: str, status: str) -> None:
    try:
        with Session(engine) as session:
            fail_snapshot(session, snapshot_id, message, status=status)
    except Exception:
        logger.exception("Could not mark snapshot %s as %s", snapshot_id, status)


def start_snapshot(
    engine: Engine,
    q: SearchQuery,
    source_name: str,
    *,
    queued: bool = False,
    watch_item_id: int | None = None,
) -> int:
    """Create the snapshot row for a search ("running", or "queued") and return its id."""
    with Session(engine) as session:
        snap = create_snapshot(
            session,
            q,
            source_name,
            status="queued" if queued else "running",
            watch_item_id=watch_item_id,
        )
    assert snap.id is not None
    return snap.id


async def run_scrape_into(
    engine: Engine,
    snapshot_id: int,
    q: SearchQuery,
    source: Source,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
) -> Snapshot:
    """Run the search for an existing snapshot, store the ads and return it finished.

    A ``SourceBlocked`` raised by the source is saved as a "blocked" snapshot. Any other
    failure marks the snapshot "error" and is raised as ``SourceError``. A Ctrl+C
    (``KeyboardInterrupt``) or task cancellation marks it "cancelled" and propagates.
    """
    try:
        try:
            result = await source.search(q, progress=progress, cancel=cancel)
        except SourceBlocked as exc:
            result = SearchResult(status="blocked", message=str(exc))
        with Session(engine) as session:
            return finish_snapshot(session, snapshot_id, result)
    except (KeyboardInterrupt, asyncio.CancelledError):
        _close_quietly(engine, snapshot_id, "Search interrupted.", "cancelled")
        raise
    except SourceError as exc:
        _close_quietly(engine, snapshot_id, str(exc), "error")
        raise
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _close_quietly(engine, snapshot_id, message, "error")
        raise SourceError(message) from exc


async def run_scrape(
    q: SearchQuery,
    source: Source,
    engine: Engine,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
) -> Snapshot:
    """Create a snapshot and run the search into it (see ``run_scrape_into``)."""
    snapshot_id = start_snapshot(engine, q, source.name)
    return await run_scrape_into(engine, snapshot_id, q, source, progress, cancel)
