"""JSON API routes (`/api/*`) and the local media route (`/media/*`)."""

import asyncio
import logging
import re
import threading
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session
from sse_starlette import EventSourceResponse

from adscout import __version__
from adscout.analysis.backends import AnalysisAvailability, analysis_status
from adscout.api.deps import ApiState, State, refuse_in_demo
from adscout.config import AnalysisBackend, AnalysisModel, Settings
from adscout.db.repo import clear_override, list_snapshots, override_key, set_override
from adscout.db.tables import Snapshot
from adscout.locks import check_profile_free, profile_lock_dir
from adscout.models import SearchQuery
from adscout.scoring.factors import FactorResult
from adscout.scoring.relevance import PAGE_KEY_PREFIX
from adscout.services.analysis import NoRelevantAds
from adscout.services.analysis_jobs import AnalysisJob, AnalysisUnavailable
from adscout.services.export import (
    RENDER_TIMEOUT_S,
    ExportError,
    ExportPageFailed,
    RenderFormat,
    export_filename,
    snapshot_csv,
)
from adscout.services.media import BLOB_DIR
from adscout.services.report import (
    Report,
    SnapshotView,
    TimelineBucketView,
    build_report,
    snapshot_view,
)
from adscout.services.searches import SearchBusy, SearchEvent
from adscout.settings_store import UserSettings, effective_settings, update_user_settings
from adscout.watch import notify

logger = logging.getLogger(__name__)

BUSY_MESSAGE = "A search is already running. Wait for it to finish or cancel it."
BROWSER_BUSY_MESSAGE = "A search is running. Open the browser once it finishes or is cancelled."
BROWSER_ALREADY_OPEN = "The AdScout browser is already open."
BROWSER_OPEN_SEARCH = "Close the AdScout browser window before starting a search."
EXPORT_TIMED_OUT = "Export timed out."
EXPORT_LOAD_FAILED = "The report could not be loaded for export."
EXPORT_BUSY = "Another export is in progress."
COMPARE_IDS = "Pick 2 to 4 different snapshots to compare."
COMPARE_MIN, COMPARE_MAX = 2, 4
SSE_PING_SECONDS = 15
MEDIA_CACHE = "public, max-age=31536000, immutable"

_MEDIA_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_SNAPSHOT_ID = re.compile(r"^[0-9]{1,18}$")

router = APIRouter(prefix="/api")
media_router = APIRouter()


class SettingsView(BaseModel):
    timezone: str
    max_results: int
    has_meta_token: bool
    version: str
    analysis: AnalysisAvailability
    analysis_backend: AnalysisBackend
    analysis_model: AnalysisModel
    auto_analyze: bool
    scroll_delay_min: float
    scroll_delay_max: float
    daily_check_time: str  # "HH:MM" in ``timezone``
    desktop_notifications: bool
    # Whether the optional desktop-notifier package is installed (the toggle needs it).
    notifications_available: bool
    max_auto_analyses_per_day: int
    # ``adscout demo``: synthetic data, live actions disabled.
    demo: bool


class SettingsUpdate(BaseModel):
    """A partial update: omitted (or null) fields keep their current value."""

    model_config = ConfigDict(extra="forbid")

    analysis_backend: AnalysisBackend | None = None
    analysis_model: AnalysisModel | None = None
    auto_analyze: bool | None = None
    scroll_delay_min: float | None = None
    scroll_delay_max: float | None = None
    max_results: int | None = None
    daily_check_time: str | None = None
    desktop_notifications: bool | None = None
    max_auto_analyses_per_day: int | None = None


class AnalysisRequest(BaseModel):
    force: bool = False


class StartSearchResponse(BaseModel):
    snapshot_id: int


class CancelResponse(BaseModel):
    cancelled: bool


Scope = Literal["ad", "advertiser"]


class ClearOverrideBody(BaseModel):
    # The snapshot whose search the override belongs to (its keyword, or its advertiser page).
    snapshot_id: int | None = None
    keyword: str | None = Field(
        default=None,
        json_schema_extra={"deprecated": True},
        description="Deprecated: send snapshot_id. A keyword, for keyword searches only.",
    )
    scope: Scope
    target_id: str


class OverrideBody(ClearOverrideBody):
    mode: Literal["exclude", "include"]


def _not_found(snapshot_id: int) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Snapshot {snapshot_id} not found.")


def _settings_view(s: Settings, *, demo: bool) -> SettingsView:
    return SettingsView(
        timezone=s.timezone,
        max_results=s.max_results,
        has_meta_token=s.meta_access_token is not None,
        version=__version__,
        analysis=analysis_status(s),
        analysis_backend=s.analysis_backend,
        analysis_model=s.analysis_model,
        auto_analyze=s.auto_analyze,
        scroll_delay_min=s.scroll_delay_min,
        scroll_delay_max=s.scroll_delay_max,
        daily_check_time=s.daily_check_time,
        desktop_notifications=s.desktop_notifications,
        notifications_available=notify.available(),
        max_auto_analyses_per_day=s.max_auto_analyses_per_day,
        demo=demo,
    )


def _validation_detail(exc: ValidationError) -> str:
    messages = [str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()]
    return " ".join(dict.fromkeys(messages))


@router.get("/settings", operation_id="getSettings", response_model=SettingsView)
def get_settings(state: State) -> SettingsView:
    return _settings_view(effective_settings(state.settings), demo=state.demo)


@router.put(
    "/settings",
    operation_id="updateSettings",
    response_model=SettingsView,
    responses={422: {"description": "Invalid settings"}},
)
def put_settings(body: SettingsUpdate, state: State) -> SettingsView:
    patch = body.model_dump(exclude_none=True)
    try:
        if ("scroll_delay_min" in patch) != ("scroll_delay_max" in patch):
            # Check one delay against the other's current value, wherever that comes from,
            # but save only what the user sent so a .env value keeps applying.
            current = effective_settings(state.settings)
            UserSettings.model_validate(
                {
                    "scroll_delay_min": current.scroll_delay_min,
                    "scroll_delay_max": current.scroll_delay_max,
                    **patch,
                }
            )
        update_user_settings(state.settings.data_dir, patch)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, _validation_detail(exc)
        ) from None
    if state.scheduler is not None:
        state.scheduler.reschedule()
    return _settings_view(effective_settings(state.settings), demo=state.demo)


@router.get("/snapshots", operation_id="listSnapshots", response_model=list[SnapshotView])
def snapshots(state: State, keyword: str | None = None) -> list[SnapshotView]:
    with Session(state.engine) as session:
        return [snapshot_view(s) for s in list_snapshots(session, keyword)]


@router.post(
    "/searches",
    operation_id="startSearch",
    status_code=status.HTTP_201_CREATED,
    response_model=StartSearchResponse,
    responses={
        403: {"description": "Demo mode: searches are off"},
        409: {"description": "A search is already running"},
    },
)
async def start_search(q: SearchQuery, state: State) -> StartSearchResponse:
    refuse_in_demo(state)
    if state.browser_open():
        raise HTTPException(status.HTTP_409_CONFLICT, BROWSER_OPEN_SEARCH)
    try:
        snapshot_id = await state.manager.start(q)
    except SearchBusy:
        raise HTTPException(status.HTTP_409_CONFLICT, BUSY_MESSAGE) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return StartSearchResponse(snapshot_id=snapshot_id)


@router.get(
    "/searches/active",
    operation_id="getActiveSearch",
    response_model=SearchEvent | None,
)
async def active_search(state: State) -> SearchEvent | None:
    return state.manager.active()


@router.get(
    "/searches/{snapshot_id}/events",
    operation_id="searchEvents",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "Server-sent events: `event: <kind>`, `data: SearchEvent` JSON.",
            "content": {"text/event-stream": {}},
        },
        404: {"description": "Unknown snapshot"},
    },
)
async def search_events(snapshot_id: int, state: State) -> EventSourceResponse:
    # Every snapshot, live or queued, has its row before it runs: check that, and only
    # subscribe once the body is streamed. A response that is never sent (the client
    # went away first) then leaves no subscriber behind.
    if not await asyncio.to_thread(_snapshot_exists, state, snapshot_id):
        raise _not_found(snapshot_id)

    async def stream() -> AsyncIterator[dict[str, str]]:
        async with aclosing(state.manager.subscribe(snapshot_id)) as events:
            try:
                async for event in events:
                    yield {"event": event.kind, "data": event.model_dump_json()}
            except LookupError:
                return  # deleted meanwhile: end the stream

    return EventSourceResponse(stream(), ping=SSE_PING_SECONDS)


def _snapshot_exists(state: ApiState, snapshot_id: int) -> bool:
    with Session(state.engine) as session:
        return session.get(Snapshot, snapshot_id) is not None


@router.post(
    "/searches/{snapshot_id}/cancel",
    operation_id="cancelSearch",
    response_model=CancelResponse,
)
async def cancel_search(snapshot_id: int, state: State) -> CancelResponse:
    return CancelResponse(cancelled=state.manager.cancel(snapshot_id))


@router.get(
    "/snapshots/{snapshot_id}/report",
    operation_id="getReport",
    response_model=Report,
    response_model_by_alias=True,
    responses={404: {"description": "Unknown snapshot"}},
)
def report(snapshot_id: int, state: State) -> Report:
    with Session(state.engine) as session:
        return _report_or_http(state, session, snapshot_id)


@router.post(
    "/snapshots/{snapshot_id}/analysis",
    operation_id="startAnalysis",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisJob,
    responses={
        403: {"description": "Demo mode: analyses are off"},
        404: {"description": "Unknown snapshot"},
        422: {"description": "No relevant ads to analyze"},
        503: {"description": "No Claude backend is available"},
    },
)
async def start_analysis(snapshot_id: int, body: AnalysisRequest, state: State) -> AnalysisJob:
    refuse_in_demo(state)
    try:
        return await state.analysis.start(snapshot_id, force=body.force)
    except LookupError:
        raise _not_found(snapshot_id) from None
    except NoRelevantAds as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except AnalysisUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.message) from None


@router.get(
    "/snapshots/{snapshot_id}/analysis",
    operation_id="getAnalysis",
    response_model=AnalysisJob | None,
    responses={404: {"description": "Unknown snapshot"}},
)
async def get_analysis(snapshot_id: int, state: State) -> AnalysisJob | None:
    try:
        return state.analysis.status(snapshot_id)
    except LookupError:
        raise _not_found(snapshot_id) from None


def _override_key(session: Session, body: ClearOverrideBody) -> str:
    """The key the override is stored under: from ``snapshot_id``, else the deprecated
    ``keyword`` (keyword searches only). Raises 404/422 ``HTTPException``s."""
    if body.snapshot_id is not None and body.keyword is not None:
        raise ValueError("Send snapshot_id or keyword, not both.")
    if body.snapshot_id is not None:
        snap = session.get(Snapshot, body.snapshot_id)
        if snap is None:
            raise _not_found(body.snapshot_id)
        key = override_key(snap)
        if not key:
            raise ValueError("keyword has no searchable words")
        return key
    if body.keyword is None:
        raise ValueError("Give the snapshot_id the override belongs to.")
    if body.keyword.strip().startswith(PAGE_KEY_PREFIX):
        raise ValueError("Advertiser overrides need the snapshot_id.")
    return body.keyword


@router.put(
    "/overrides",
    operation_id="setOverride",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def put_override(body: OverrideBody, state: State) -> None:
    try:
        with Session(state.engine) as session:
            key = _override_key(session, body)
            set_override(session, key, body.scope, body.target_id, body.mode)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.delete(
    "/overrides",
    operation_id="clearOverride",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_override(body: ClearOverrideBody, state: State) -> None:
    try:
        with Session(state.engine) as session:
            clear_override(session, _override_key(session, body), body.scope, body.target_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


class CompareLeader(BaseModel):
    advertiser_key: str
    page_name: str
    ads: int
    creatives: int
    score: int


class CompareAnalysis(BaseModel):
    verdict: str


class CompareColumn(BaseModel):
    """One snapshot's column on the compare screen."""

    snapshot: SnapshotView
    score: int  # headline Winner Score (the leader's)
    market_score: int  # pooled over every relevant advertiser
    provisional: bool
    relevant_ads: int
    advertisers: int
    creatives: int
    leader: CompareLeader | None
    leader_factors: list[FactorResult]  # empty without a leader
    market_factors: list[FactorResult]
    timeline_month: list[TimelineBucketView]
    analysis: CompareAnalysis | None  # the latest analysis' verdict, if analysed


def _compare_ids(raw: str) -> list[int]:
    parts = raw.split(",")
    if not all(p.strip().isdigit() for p in parts):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, COMPARE_IDS)
    ids = [int(p) for p in parts]
    if len(set(ids)) != len(ids) or not COMPARE_MIN <= len(ids) <= COMPARE_MAX:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, COMPARE_IDS)
    return ids


def _compare_column(r: Report) -> CompareColumn:
    leader = r.score.leader
    return CompareColumn(
        snapshot=r.snapshot,
        score=r.score.headline,
        market_score=r.score.total,
        provisional=r.score.provisional,
        relevant_ads=r.score.relevant_ads,
        advertisers=r.score.advertisers,
        creatives=r.score.creatives,
        leader=None if leader is None else CompareLeader.model_validate(leader.model_dump()),
        leader_factors=[] if leader is None else leader.factors,
        market_factors=r.score.factors,
        timeline_month=r.timeline_month,
        analysis=(
            None if r.analysis is None else CompareAnalysis(verdict=r.analysis.result.verdict)
        ),
    )


def _report_or_http(state: ApiState, session: Session, snapshot_id: int) -> Report:
    try:
        return build_report(session, snapshot_id, state.now(), ZoneInfo(state.settings.timezone))
    except LookupError:
        raise _not_found(snapshot_id) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get(
    "/compare",
    operation_id="compareSnapshots",
    response_model=list[CompareColumn],
    responses={
        404: {"description": "Unknown snapshot"},
        422: {"description": "Not 2 to 4 different snapshot ids"},
    },
)
def compare(state: State, ids: str = "") -> list[CompareColumn]:
    """Side by side, in the order given: ``ids`` is 2 to 4 comma-separated snapshot ids."""
    wanted = _compare_ids(ids)
    with Session(state.engine) as session:
        return [_compare_column(_report_or_http(state, session, sid)) for sid in wanted]


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.get(
    "/snapshots/{snapshot_id}/export.csv",
    operation_id="exportCsv",
    response_class=Response,
    responses={
        200: {
            "description": "Every ad, UTF-8 CSV with a BOM",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        },
        404: {"description": "Unknown snapshot"},
    },
)
def export_csv(snapshot_id: int, state: State) -> Response:
    with Session(state.engine) as session:
        report = _report_or_http(state, session, snapshot_id)
    data = snapshot_csv(report, ZoneInfo(state.settings.timezone))
    name = export_filename(report.snapshot.query, snapshot_id, "csv")
    return Response(data, media_type="text/csv; charset=utf-8", headers=_attachment(name))


_RENDER_MEDIA: dict[RenderFormat, str] = {"pdf": "application/pdf", "png": "image/png"}


def _snapshot_query(state: ApiState, snapshot_id: int) -> str | None:
    with Session(state.engine) as session:
        snap = session.get(Snapshot, snapshot_id)
        return None if snap is None else snap.query


async def _rendered(state: ApiState, snapshot_id: int, fmt: RenderFormat) -> Response:
    query = await asyncio.to_thread(_snapshot_query, state, snapshot_id)
    if query is None:
        raise _not_found(snapshot_id)
    if state.export_slot.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, EXPORT_BUSY)
    try:
        async with state.export_slot:
            # The renderer has its own 30 s limit; this one also covers a hung launch.
            data = await asyncio.wait_for(
                state.render_report(state.export_base_url, snapshot_id, fmt),
                RENDER_TIMEOUT_S + 5,
            )
    except TimeoutError:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, EXPORT_TIMED_OUT) from None
    except ExportPageFailed as exc:
        if exc.reason == "not-found":
            raise _not_found(snapshot_id) from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, EXPORT_LOAD_FAILED) from None
    except ExportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    name = export_filename(query, snapshot_id, fmt)
    return Response(data, media_type=_RENDER_MEDIA[fmt], headers=_attachment(name))


def _render_responses(media_type: str) -> dict[int | str, dict[str, Any]]:
    return {
        200: {
            "description": "The report's print page, rendered",
            "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "Unknown snapshot"},
        409: {"description": "Another export is in progress."},
        502: {"description": "The report could not be loaded for export."},
        503: {"description": "The export browser failed (e.g. Chromium is not installed)"},
        504: {"description": "Export timed out."},
    }


@router.get(
    "/snapshots/{snapshot_id}/export.pdf",
    operation_id="exportPdf",
    response_class=Response,
    responses=_render_responses("application/pdf"),
)
async def export_pdf(snapshot_id: int, state: State) -> Response:
    """A4 portrait, one page."""
    return await _rendered(state, snapshot_id, "pdf")


@router.get(
    "/snapshots/{snapshot_id}/export.png",
    operation_id="exportPng",
    response_class=Response,
    responses=_render_responses("image/png"),
)
async def export_png(snapshot_id: int, state: State) -> Response:
    """1600 px wide, full page."""
    return await _rendered(state, snapshot_id, "png")


def _run_opener(state: ApiState) -> None:
    try:
        state.open_browser()
    except Exception:
        logger.exception("The AdScout browser failed to open")


@router.post(
    "/browser/open",
    operation_id="openBrowser",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses={
        403: {"description": "Demo mode: the browser is off"},
        409: {
            "description": "A search is running, the browser is already open, or another "
            "AdScout process is using the browser profile"
        },
    },
)
async def open_browser(state: State) -> None:
    refuse_in_demo(state)
    if state.manager.active() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, BROWSER_BUSY_MESSAGE)
    if state.browser_open():
        raise HTTPException(status.HTTP_409_CONFLICT, BROWSER_ALREADY_OPEN)
    # Another process (a CLI scrape) holding the profile: ProfileBusy -> 409 (app handler).
    check_profile_free(profile_lock_dir(state.settings.browser_profile_dir))
    # Its own thread (and event loop): the window stays open until the user closes it.
    thread = threading.Thread(
        target=_run_opener, args=(state,), name="adscout-browser", daemon=True
    )
    state.browser_thread = thread
    thread.start()


def _file_in(base: Path, filename: str) -> Path | None:
    if not _MEDIA_NAME.fullmatch(filename):
        return None
    base = base.resolve()
    target = (base / filename).resolve()
    if target.parent != base or not target.is_file():
        return None
    return target


def blob_file(media_dir: Path, filename: str) -> Path | None:
    """The file ``media_dir/blobs/<filename>``, or None if the name is unsafe/missing."""
    return _file_in(media_dir / BLOB_DIR, filename)


def media_file(media_dir: Path, snapshot_id: str, filename: str) -> Path | None:
    """The file ``media_dir/<snapshot_id>/<filename>``, or None if the name is unsafe/missing."""
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        return None
    return _file_in(media_dir / str(int(snapshot_id)), filename)


# Before the per-snapshot route: "blobs" is not a snapshot id.
@media_router.get(f"/media/{BLOB_DIR}/{{filename}}", include_in_schema=False)
def media_blob(filename: str, state: State) -> FileResponse:
    path = blob_file(state.settings.media_dir, filename)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media file not found.")
    return FileResponse(path, headers={"Cache-Control": MEDIA_CACHE})


@media_router.get("/media/{snapshot_id}/{filename}", include_in_schema=False)
def media(snapshot_id: str, filename: str, state: State) -> FileResponse:
    path = media_file(state.settings.media_dir, snapshot_id, filename)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media file not found.")
    return FileResponse(path, headers={"Cache-Control": MEDIA_CACHE})
