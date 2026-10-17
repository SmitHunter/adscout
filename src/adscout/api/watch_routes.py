"""Watchlist and alert routes (`/api/watch*`, `/api/alerts*`)."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Annotated, Self
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import Session

from adscout.api.deps import ApiState, State, refuse_in_demo
from adscout.db.repo import (
    create_watch_item,
    delete_watch_item,
    find_watch_item,
    get_watch_item,
    list_watch_items,
    mark_read,
    update_watch_item,
)
from adscout.db.tables import WatchItem
from adscout.locks import ProfileBusy, check_profile_free, profile_lock_dir
from adscout.models import SearchQuery
from adscout.services.searches import SearchBusy
from adscout.services.watchlist import (
    AlertView,
    WatchItemView,
    WatchTrend,
    alert_views,
    busy_watch_items,
    item_trend,
    watch_item_views,
)
from adscout.settings_store import effective_settings

logger = logging.getLogger(__name__)

ALREADY_WATCHING = "Already watching this."
DELETE_WHILE_RUNNING = "This item is being checked. Try again when it finishes."
BROWSER_OPEN_CHECK = "Close the AdScout browser window before running a check."
# GET /api/alerts returns at most this many (newest first) unless asked for more.
ALERTS_DEFAULT_LIMIT = 200
ALERTS_MAX_LIMIT = 1000

router = APIRouter(prefix="/api")


class WatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class RunNowResponse(BaseModel):
    message: str  # the toast: "Checking {query} now."


class MarkReadBody(BaseModel):
    """Exactly one of ``ids`` (those alerts) or ``all: true`` (every alert)."""

    model_config = ConfigDict(extra="forbid")

    ids: list[int] | None = None
    all: bool = False

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.ids is not None) == self.all:
            raise ValueError("Send either ids or all: true.")
        return self


class MarkReadResponse(BaseModel):
    marked: int  # alerts that were unread


def _item_not_found(item_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Watch item {item_id} not found.")


def _tz(state: ApiState) -> ZoneInfo:
    return ZoneInfo(state.settings.timezone)


def _running_ids(state: ApiState) -> set[int]:
    return {i for i, t in state.watch_tasks.items() if not t.done()}


def _profile_held_elsewhere(state: ApiState) -> bool:
    """Whether another process holds the browser profile right now."""
    if state.browser_open() or state.manager.active() is not None:
        return False  # this process holds it
    try:
        check_profile_free(profile_lock_dir(state.settings.browser_profile_dir))
    except ProfileBusy:
        return True
    return False


def _busy_ids(state: ApiState, session: Session) -> set[int]:
    """Watch items being checked.

    This process's checks come from live state ("Run now" tasks and the search manager's
    running/queued searches), not DB status, which can be stale (a row left by a process
    that crashed). A DB "queued"/"running" row of another process counts only while some
    other process holds the browser profile (e.g. ``adscout watch run ID``).
    """
    busy = _running_ids(state) | state.manager.live_watch_items()
    foreign = busy_watch_items(session, state.manager.live_snapshot_ids()) - busy
    if foreign and _profile_held_elsewhere(state):
        busy |= foreign
    return busy


def _next_run(state: ApiState) -> datetime | None:
    if state.scheduler is not None:
        return state.scheduler.next_run_time()
    if state.demo:
        # The demo never runs checks, but its watchlist reads like the real app's: the next
        # daily slot is shown (instead of "paused") so the page agrees with its toggles.
        from adscout.watch.scheduler import daily_time, last_slot

        settings = effective_settings(state.settings)
        at = daily_time(settings.daily_check_time)
        return last_slot(state.now(), at, ZoneInfo(settings.timezone)) + timedelta(days=1)
    return None


def _views(state: ApiState, session: Session, items: list[WatchItem]) -> list[WatchItemView]:
    return watch_item_views(
        session,
        items,
        state.now(),
        _tz(state),
        next_run=_next_run(state),
        running_ids=_busy_ids(state, session),
    )


def _view(state: ApiState, session: Session, item: WatchItem) -> WatchItemView:
    return _views(state, session, [item])[0]


@router.get("/watch", operation_id="listWatchItems", response_model=list[WatchItemView])
def list_watch(state: State) -> list[WatchItemView]:
    with Session(state.engine) as session:
        return _views(state, session, list_watch_items(session))


@router.post(
    "/watch",
    operation_id="createWatchItem",
    status_code=status.HTTP_201_CREATED,
    response_model=WatchItemView,
    responses={409: {"description": "This search is already watched"}},
)
def create_watch(q: SearchQuery, state: State) -> WatchItemView:
    """Watch a keyword or advertiser search daily (``limit`` is ignored: the max-results
    setting applies at run time)."""
    with Session(state.engine) as session:
        if find_watch_item(session, q) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_WATCHING)
        item = create_watch_item(session, q)
        return _view(state, session, item)


@router.patch(
    "/watch/{item_id}",
    operation_id="updateWatchItem",
    response_model=WatchItemView,
    responses={404: {"description": "Unknown watch item"}},
)
def patch_watch(item_id: int, body: WatchUpdate, state: State) -> WatchItemView:
    with Session(state.engine) as session:
        try:
            item = update_watch_item(session, item_id, enabled=body.enabled)
        except LookupError:
            raise _item_not_found(item_id) from None
        return _view(state, session, item)


@router.delete(
    "/watch/{item_id}",
    operation_id="deleteWatchItem",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        404: {"description": "Unknown watch item"},
        409: {"description": "The item is being checked"},
    },
)
def delete_watch(item_id: int, state: State) -> None:
    """Stop watching. Refused while the item is being checked, so a finishing check never
    raises an alert for a deleted item."""
    with Session(state.engine) as session:
        if item_id in _busy_ids(state, session):
            raise HTTPException(status.HTTP_409_CONFLICT, DELETE_WHILE_RUNNING)
        if not delete_watch_item(session, item_id):
            raise _item_not_found(item_id)


async def _run_now(state: ApiState, item_id: int) -> None:
    try:
        await state.watch_runner.run_one(item_id)
    except SearchBusy:
        logger.info("Run now for watch item %s stopped: AdScout is shutting down", item_id)
    except LookupError:
        logger.info("Watch item %s was removed before its check", item_id)
    except Exception:
        logger.exception("Run now failed for watch item %s", item_id)


def _load_for_run(state: ApiState, item_id: int) -> tuple[WatchItem | None, bool]:
    with Session(state.engine) as session:
        return get_watch_item(session, item_id), item_id in _busy_ids(state, session)


@router.post(
    "/watch/{item_id}/run",
    operation_id="runWatchItem",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunNowResponse,
    responses={
        403: {"description": "Demo mode: checks are off"},
        404: {"description": "Unknown watch item"},
        409: {"description": "This item is already being checked, or the browser is open"},
    },
)
async def run_watch(item_id: int, state: State) -> RunNowResponse:
    """Check one item now, in the background (queued behind a running search)."""
    refuse_in_demo(state)
    item, busy = await asyncio.to_thread(_load_for_run, state, item_id)
    if item is None:
        raise _item_not_found(item_id)
    if busy or item_id in _running_ids(state):  # re-checked: a task may start meanwhile
        raise HTTPException(status.HTTP_409_CONFLICT, f"{item.query} is already being checked.")
    if state.browser_open():
        raise HTTPException(status.HTTP_409_CONFLICT, BROWSER_OPEN_CHECK)
    task = asyncio.create_task(_run_now(state, item_id), name=f"adscout-watch-run-{item_id}")
    state.watch_tasks[item_id] = task

    def forget(done: "asyncio.Task[None]") -> None:
        if state.watch_tasks.get(item_id) is done:
            del state.watch_tasks[item_id]

    task.add_done_callback(forget)
    return RunNowResponse(message=f"Checking {item.query} now.")


@router.get(
    "/watch/{item_id}/trend",
    operation_id="getWatchTrend",
    response_model=WatchTrend,
    responses={404: {"description": "Unknown watch item"}},
)
def watch_trend(item_id: int, state: State) -> WatchTrend:
    with Session(state.engine) as session:
        item = get_watch_item(session, item_id)
        if item is None:
            raise _item_not_found(item_id)
        return WatchTrend(
            item=_view(state, session, item),
            points=item_trend(session, item_id, state.now(), _tz(state)),
        )


@router.get("/alerts", operation_id="listAlerts", response_model=list[AlertView])
def list_alerts_route(
    state: State,
    unread: bool = False,
    limit: Annotated[int, Query(ge=1, le=ALERTS_MAX_LIMIT)] = ALERTS_DEFAULT_LIMIT,
) -> list[AlertView]:
    """The newest ``limit`` alerts (unread ones only with ``unread=true``)."""
    with Session(state.engine) as session:
        return alert_views(session, unread_only=unread, limit=limit)


@router.post("/alerts/read", operation_id="markAlertsRead", response_model=MarkReadResponse)
def mark_alerts_read(body: MarkReadBody, state: State) -> MarkReadResponse:
    with Session(state.engine) as session:
        return MarkReadResponse(marked=mark_read(session, "all" if body.all else body.ids or []))
