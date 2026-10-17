"""The FastAPI application: API routes, media, and the built single-page app."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from adscout import __version__
from adscout.api.deps import (
    ApiState,
    BrowserOpener,
    WatchRunnerLike,
    WatchSchedulerFactory,
    build_analysis_manager,
    build_manager,
    build_watch_runner,
)
from adscout.api.routes import media_router, router
from adscout.api.watch_routes import router as watch_router
from adscout.config import Settings
from adscout.db.repo import make_engine, sweep_running
from adscout.locks import ProfileBusy, acquire_profile, profile_lock, profile_lock_dir
from adscout.services.analysis_jobs import AnalysisManager
from adscout.services.export import ReportRenderer
from adscout.services.export import render_report as default_render_report
from adscout.services.searches import SearchManager

logger = logging.getLogger(__name__)

BUILD_HINT = "Frontend not built. Run: npm --prefix web ci && npm --prefix web run build"

# Paths the SPA fallback must never answer with index.html.
_RESERVED_PREFIXES = ("api", "media")
_RESERVED_PATHS = frozenset({"openapi.json", "docs", "redoc", "docs/oauth2-redirect"})

# The server only ever answers to loopback names: blocks DNS-rebinding attacks.
# Tests pass their client's host ("test", "testserver") through ``create_app``.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
DEFAULT_PORT = 8787
# The Vite dev server (``npm --prefix web run dev``) proxies the API from this port.
VITE_DEV_PORT = 5173
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# No port in an http Origin means port 80.
_LOCAL_ORIGIN = re.compile(r"http://(?:127\.0\.0\.1|localhost)(?::(\d{1,5}))?")
_DEFAULT_HTTP_PORT = 80


def _header(scope: Scope, name: bytes) -> bytes | None:
    return next((v for k, v in scope["headers"] if k == name), None)


class LocalOriginMiddleware:
    """Reject requests a browser sends on behalf of another site.

    State-changing requests are blocked unless their ``Origin`` is this server's own
    loopback address (``ports``: the bound port and the Vite dev port), so another local
    app on some other port can't spend the user's quota either. So is any ``/api/``
    request the browser marks ``Sec-Fetch-Site: cross-site`` (e.g. an ``<img>`` on another
    site pointing at an export, which would launch Chromium). Requests without these
    headers (CLI, curl, tests) and same-origin ones pass untouched.
    """

    def __init__(self, app: ASGIApp, ports: Collection[int]) -> None:
        self.app = app
        self._ports = frozenset(ports)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._blocked(scope):
            response = JSONResponse(
                {"detail": "Cross-site request blocked."},
                status_code=status.HTTP_403_FORBIDDEN,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _blocked(self, scope: Scope) -> bool:
        if str(scope["path"]).startswith("/api/") and _header(scope, b"sec-fetch-site") == (
            b"cross-site"
        ):
            return True
        if scope["method"] in _SAFE_METHODS:
            return False
        origin = _header(scope, b"origin")
        if origin is None:
            return False
        match = _LOCAL_ORIGIN.fullmatch(origin.decode("latin-1"))
        if match is None:
            return True
        port = int(match[1]) if match[1] is not None else _DEFAULT_HTTP_PORT
        return port not in self._ports


def _default_opener(settings: Settings) -> BrowserOpener:
    def open_profile() -> None:  # pragma: no cover - launches a real browser
        from adscout.sources.meta_web.driver import open_until_closed
        from adscout.sources.meta_web.urls import LIBRARY_URL

        asyncio.run(open_until_closed(settings.browser_profile_dir, LIBRARY_URL))

    return open_profile


def _startup_sweep(settings: Settings, engine: Engine) -> None:
    """Close snapshots a previous process left "queued" or "running".

    "queued" rows are always closed: only a server queues searches, and the server lock
    means this is the only one. "running" rows are left alone while another process holds
    the browser profile (a CLI scrape whose live snapshot must not be closed).
    """
    lock = profile_lock(profile_lock_dir(settings.browser_profile_dir), timeout_s=0)
    try:
        acquire_profile(lock)
    except ProfileBusy:
        logger.info(
            "Startup sweep skipped running searches: another AdScout process is using the browser."
        )
        with Session(engine) as session:
            sweep_running(session, ("queued",))
        return
    try:
        with Session(engine) as session:
            sweep_running(session)
    finally:
        lock.release()


async def _cancel_watch_tasks(state: ApiState) -> None:
    """Stop "Run now" checks still waiting on their search (the manager cancels the search)."""
    tasks = [t for t in state.watch_tasks.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=5)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_reserved(path: str) -> bool:
    head = path.split("/", 1)[0]
    return head in _RESERVED_PREFIXES or path in _RESERVED_PATHS


_SPA_PATH = "/{path:path}"


def _known_path(app: FastAPI, scope: Scope) -> bool:
    """Whether a route other than the SPA fallback serves this path (with any method)."""
    for route in app.router.routes:
        if getattr(route, "path", None) == _SPA_PATH:
            continue
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return True
    return False


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    index = root / "index.html"
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.api_route(_SPA_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if _is_reserved(path):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        # index.html changes with every build; the hashed assets carry the caching.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(
    settings: Settings,
    engine: Engine | None = None,
    manager: SearchManager | None = None,
    static_dir: Path | None = None,
    *,
    analysis: AnalysisManager | None = None,
    open_browser: BrowserOpener | None = None,
    now: Callable[[], datetime] | None = None,
    watch_scheduler: WatchSchedulerFactory | None = None,
    watch_runner: WatchRunnerLike | None = None,
    render_report: ReportRenderer | None = None,
    port: int = DEFAULT_PORT,
    allowed_hosts: list[str] | None = None,
    demo: bool = False,
) -> FastAPI:
    """Build the app. Missing pieces default to the real ones built from ``settings``.

    ``watch_scheduler`` builds the daily watchlist scheduler, started with the app. Only
    ``adscout serve`` passes it, once it holds the server lock: any other app (tests, a
    second instance) never runs scheduled searches. ``watch_runner`` runs "Run now"
    checks (and the scheduler's daily run); ``render_report`` renders PDF/PNG exports from
    ``http://127.0.0.1:{port}``, the server's own address. ``allowed_hosts`` replaces the
    loopback-only Host allow-list (tests add their client's host).

    ``demo`` serves ``adscout demo``'s synthetic data: searches, watch checks, analyses and
    the browser window answer 403 with the demo copy, and the scheduler never starts.
    """
    engine = engine if engine is not None else make_engine(settings.db_path)
    now = now if now is not None else _now
    if analysis is None:
        analysis = build_analysis_manager(settings, engine, now)
    if manager is None:
        manager = build_manager(settings, engine, analysis)
    state = ApiState(
        settings=settings,
        engine=engine,
        manager=manager,
        analysis=analysis,
        open_browser=open_browser if open_browser is not None else _default_opener(settings),
        now=now,
        watch_runner=(
            watch_runner
            if watch_runner is not None
            # ``state`` is read when the runner asks, after it exists.
            else build_watch_runner(settings, engine, manager, now, lambda: state.browser_open())
        ),
        render_report=render_report if render_report is not None else default_render_report,
        export_base_url=f"http://127.0.0.1:{port}",
        demo=demo,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _startup_sweep(state.settings, state.engine)
        if watch_scheduler is not None and not demo:
            state.scheduler = watch_scheduler(state)
            await state.scheduler.start()
        try:
            yield
        finally:
            if state.scheduler is not None:
                await state.scheduler.shutdown()
            await _cancel_watch_tasks(state)
            # Searches first: a search ending during shutdown must not start an analysis
            # after the analysis manager has stopped.
            await state.manager.shutdown()
            await state.analysis.shutdown()

    app = FastAPI(title="AdScout", version=__version__, lifespan=lifespan)
    app.state.api = state
    # Last added runs first: host check, then the origin check.
    app.add_middleware(LocalOriginMiddleware, ports={port, VITE_DEV_PORT})
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts if allowed_hosts is not None else ALLOWED_HOSTS,
    )
    app.include_router(router)
    app.include_router(watch_router)
    app.include_router(media_router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        # The SPA fallback only takes GET/HEAD, so a POST to an unknown /api/ path would
        # match it by path alone and answer 405. Unknown is unknown: 404.
        if (
            exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
            and _is_reserved(request.url.path.lstrip("/"))
            and not _known_path(app, request.scope)
        ):
            exc = StarletteHTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        return await http_exception_handler(request, exc)

    @app.exception_handler(ProfileBusy)
    async def profile_busy(request: Request, exc: ProfileBusy) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_409_CONFLICT)

    if static_dir is not None and (static_dir / "index.html").is_file():
        _mount_spa(app, static_dir)
    else:

        @app.get("/", include_in_schema=False)
        def build_hint() -> PlainTextResponse:
            return PlainTextResponse(BUILD_HINT, status_code=status.HTTP_404_NOT_FOUND)

    return app
