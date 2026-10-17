import asyncio
import codecs
import io
import logging
import signal
import socket
import sys
import threading
import time
import unicodedata
import webbrowser
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple, Protocol
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy import Engine
from sqlmodel import Session

from adscout import __version__
from adscout.analysis.backends import (
    AnalysisError,
    BackendName,
    analysis_status,
    resolve_analyzer,
)
from adscout.analysis.prompt import PROMPT_VERSION, input_hash
from adscout.config import Settings, get_settings
from adscout.db.repo import (
    clear_override,
    create_watch_item,
    delete_watch_item,
    get_analysis,
    list_snapshots,
    list_watch_items,
    load_ads,
    make_engine,
    override_key,
    set_override,
    snapshot_keywords,
    update_watch_item,
)
from adscout.db.tables import Snapshot, WatchItem
from adscout.demo import DEMO_COPY, DEMO_DATA_DIR, DEMO_MARKER, DEMO_PORT
from adscout.locks import (
    PROFILE_BUSY_MESSAGE,
    ProfileBusy,
    read_server_port,
    server_lock,
    single_instance_message,
    write_server_port,
)
from adscout.models import Ad, ScrapeProgress, SearchQuery
from adscout.scoring import FactorResult, TimelineBucket, WinnerScore
from adscout.scoring.relevance import PAGE_KEY_PREFIX, require_keyword
from adscout.scoring.score import off_topic_warning
from adscout.services.analysis import NO_RELEVANT, NoRelevantAds, analysis_input, run_analysis
from adscout.services.report import AdvertiserRow, AnalysisView, Report, build_report
from adscout.services.scrape import run_scrape
from adscout.settings_store import effective_settings
from adscout.sources import CancelToken, Source, SourceError
from adscout.sources.select import get_source, unsupported_message

if TYPE_CHECKING:
    from adscout.services.media import MediaDownloader
    from adscout.watch.runner import ItemRun, RunSummary

app = typer.Typer(help="AdScout: scrape the Meta Ad Library and score products.")

_TIME_FMT = "%Y-%m-%d %H:%M"


class SourceChoice(StrEnum):
    auto = "auto"
    web = "web"
    api = "api"


class BackendChoice(StrEnum):
    auto = "auto"
    cli = "cli"
    api = "api"


class ModelChoice(StrEnum):
    sonnet = "claude-sonnet-5-5"
    opus = "claude-opus-5-5"
    haiku = "claude-haiku-5-5"


def _now() -> datetime:
    return datetime.now(UTC)


def get_engine() -> Engine:
    return make_engine(get_settings().db_path)


DEMO_SERVE_COPY = "This folder holds demo data. Run uv run adscout demo to explore it."


def _refuse_demo_folder(*, serving: bool = False) -> None:
    """Live commands never run on a demo folder (they would reach Meta or Claude)."""
    if (get_settings().data_dir / DEMO_MARKER).exists():
        typer.echo(DEMO_SERVE_COPY if serving else DEMO_COPY)
        raise typer.Exit(1)


def _console() -> Console:
    # emoji=False: page names like "Shop :fire:" must print literally (escape() leaves
    # shortcodes alone), and expanded emoji can't be encoded on cp1252 consoles.
    return Console(emoji=False)


# Plain-ASCII stand-ins for the typographic characters AdScout and Rich print. The
# ellipsis stays one character wide so truncated table cells keep their alignment.
_ASCII_STAND_INS = {
    "\u2026": "~",  # ellipsis (Rich's truncation mark)
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2192": "->",  # arrow, as in alerts: "(10 -> 20)" from "(10 \u2192 20)"
    "\u00b7": "-",  # middle dot
}
ASCII_ERRORS = "adscout-ascii"


def _ascii_char(ch: str) -> str:
    if ch in _ASCII_STAND_INS:
        return _ASCII_STAND_INS[ch]
    # Accented letters keep their base letter ("Café" -> "Cafe"); emoji and CJK become "?".
    base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
    return base or "?"


def _ascii_stand_in(exc: UnicodeError) -> tuple[str, int]:
    """Codec error handler: an ASCII stand-in for each character ASCII can't carry."""
    if not isinstance(exc, UnicodeEncodeError):
        raise exc
    chunk = exc.object[exc.start : exc.end]
    return "".join(_ascii_char(ch) for ch in chunk), exc.end


codecs.register_error(ASCII_ERRORS, _ascii_stand_in)


def _tolerate_narrow_stdout() -> None:
    """Make every command print safely when stdout is not UTF (cp1252 pipes on Windows).

    A Windows terminal gets UTF-8. Anything else non-UTF (a pipe or a file: PowerShell
    capturing output, ``> out.txt``) gets plain ASCII: whoever reads those bytes may decode
    them as UTF-8, and a cp1252 byte such as Rich's ellipsis would show as U+FFFD.
    Rich then draws ASCII tables, and characters the stream can't carry (emoji,
    CJK in page names) print as "?" instead of crashing the command.
    """
    stream = sys.stdout
    if not isinstance(stream, io.TextIOWrapper):
        return
    try:
        "🔥".encode(stream.encoding)
        return  # a UTF encoding: nothing to do
    except UnicodeEncodeError:
        pass
    except LookupError:
        return
    if sys.platform == "win32" and stream.isatty():
        stream.reconfigure(encoding="utf-8", errors="replace")
    else:
        stream.reconfigure(encoding="ascii", errors=ASCII_ERRORS)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"adscout {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """AdScout command line interface. With no command, starts the GUI server (serve)."""
    _tolerate_narrow_stdout()
    if ctx.invoked_subcommand is None:
        serve(port=DEFAULT_PORT, no_open=False)


@contextmanager
def _ctrl_c_cancels(cancel: CancelToken) -> Iterator[None]:
    """First Ctrl+C asks the search to stop (partial results kept); the second aborts.

    Uses ``signal.signal`` (works on Windows, unlike ``loop.add_signal_handler``) and
    restores the previous handler afterwards.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)

    def handler(signum: int, frame: FrameType | None) -> None:
        if cancel.cancelled:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt
        cancel.cancel()

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _local(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime(_TIME_FMT)


def _search_query(
    console: Console,
    fields: dict[str, object],
    keyword: str | None,
    advertiser: str | None,
    name: str | None,
    country: str | None,
) -> SearchQuery:
    """The keyword or advertiser search the arguments describe; exits 1 when invalid."""
    fields = dict(fields)
    if country is not None:
        fields["country"] = country
    if advertiser is not None:
        page = advertiser.strip()
        fields.update(kind="advertiser", page_id=page, keyword=name or keyword or f"Page {page}")
    elif name is not None:
        console.print("[red]--name is only used with --advertiser PAGE_ID.[/red]")
        raise typer.Exit(1)
    elif keyword is None:
        console.print("[red]Give a keyword, or --advertiser PAGE_ID.[/red]")
        raise typer.Exit(1)
    else:
        fields["keyword"] = keyword
    try:
        return SearchQuery.model_validate(fields)
    except ValidationError as exc:
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "search"
            console.print(f"[red]Invalid {escape(field)}: {escape(err['msg'])}[/red]")
        raise typer.Exit(1) from None


async def _scrape(
    q: SearchQuery, source: Source, engine: Engine, cancel: CancelToken, console: Console
) -> Snapshot:
    with console.status("Starting…") as status:

        def on_progress(p: ScrapeProgress) -> None:
            stopping = " (stopping, Ctrl+C again to abort)" if cancel.cancelled else ""
            status.update(
                f"Found {p.ads_found} ads · {p.advertisers} advertisers · {p.phase}…{stopping}"
            )

        return await run_scrape(q, source, engine, progress=on_progress, cancel=cancel)


def _print_summary(console: Console, snap: Snapshot, ads: list[Ad], tz: ZoneInfo) -> None:
    now = datetime.now(UTC)
    table = Table(
        title=escape(f'Snapshot #{snap.id} · "{snap.query}" · {snap.country}'),
        show_header=False,
        title_justify="left",
    )
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Status", escape(snap.status))
    table.add_row("Ads", str(snap.ad_count))
    table.add_row("Advertisers", str(snap.advertiser_count))
    if ads:
        oldest = min(ads, key=lambda a: a.start_date)
        table.add_row(
            "Oldest ad",
            f"{oldest.start_date.astimezone(tz):%Y-%m-%d} "
            f"({oldest.days_running(now)} days, {escape(oldest.page_name)})",
        )
    table.add_row("Started", _local(snap.started_at, tz))
    console.print(table)

    names: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for ad in ads:
        key = ad.advertiser_key
        if key is None:
            continue
        names.setdefault(key, ad.page_name.strip() or ad.page_id)
        counts[key] += 1
    if counts:
        top = Table(title="Top advertisers", title_justify="left")
        top.add_column("Advertiser")
        top.add_column("Ads", justify="right")
        for key, n in counts.most_common(5):
            top.add_row(escape(names[key]), str(n))
        console.print(top)


@app.command()
def scrape(
    keyword: Annotated[
        str | None,
        typer.Argument(help='Search keyword, e.g. "posture corrector" (not with --advertiser).'),
    ] = None,
    advertiser: Annotated[
        str | None,
        typer.Option(
            "--advertiser",
            metavar="PAGE_ID",
            help="Scrape one advertiser's page instead: its numeric Facebook page id.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(help="The advertiser's display name (with --advertiser)."),
    ] = None,
    country: Annotated[
        str | None,
        typer.Option(
            help="Two-letter country code (GB for the UK, EU for all EU countries). "
            "Default: US, or ALL for --advertiser."
        ),
    ] = None,
    all_ads: Annotated[
        bool, typer.Option("--all", help="Include inactive ads (default: active only).")
    ] = False,
    media: Annotated[str, typer.Option(help="all, image, video or meme.")] = "all",
    limit: Annotated[int, typer.Option(help="Maximum number of ads to collect.")] = 300,
    headless: Annotated[
        bool | None,
        typer.Option(
            "--headless/--no-headless",
            help="Run the browser with or without a window (default: HEADLESS setting).",
        ),
    ] = None,
    source_choice: Annotated[
        SourceChoice,
        typer.Option(
            "--source",
            help="auto: official API for EU/UK when META_ACCESS_TOKEN is set, else web scraper.",
        ),
    ] = SourceChoice.auto,
) -> None:
    """Search the Meta Ad Library and save the ads as a snapshot."""
    _refuse_demo_folder()
    console = _console()
    settings = effective_settings(get_settings())
    if source_choice is SourceChoice.api and settings.meta_access_token is None:
        console.print("[red]META_ACCESS_TOKEN is not set. Add it to .env (see .env.example).[/red]")
        raise typer.Exit(1)
    if headless is not None:
        settings = settings.model_copy(update={"headless": headless})
    fields: dict[str, object] = {
        "active_status": "all" if all_ads else "active",
        "media_type": media,  # validated by pydantic
        "limit": limit,
    }
    q = _search_query(console, fields, keyword, advertiser, name, country)

    source = get_source(q, settings, source_choice.value)
    if not source.supports(q):
        console.print(f"[red]{escape(unsupported_message(q, source, settings))}[/red]")
        raise typer.Exit(1)

    engine = get_engine()
    cancel = CancelToken()
    try:
        with _ctrl_c_cancels(cancel):
            snap = asyncio.run(_scrape(q, source, engine, cancel, console))
    except ProfileBusy as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from None
    except SourceError as exc:
        console.print(f"[red]Scrape failed: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(130) from None

    assert snap.id is not None
    with Session(engine) as session:
        ads = load_ads(session, snap.id)
    tz = ZoneInfo(settings.timezone)

    if not ads and snap.status == "ok":
        if q.kind == "advertiser":
            empty = f'No ads found for advertiser "{q.keyword}" (page {q.page_id}) in {q.country}.'
        else:
            empty = f'No ads found for "{q.keyword}" in {q.country}. Try a broader keyword.'
        console.print(escape(empty))
        return
    if ads:
        _print_summary(console, snap, ads, tz)
    if snap.status == "blocked":
        blocked = snap.error_message or "Meta blocked the search."
        console.print(f"[yellow]{escape(blocked)}[/yellow]")
        raise typer.Exit(2)
    if snap.status == "cancelled":
        console.print("[yellow]Search cancelled; partial results saved.[/yellow]")
    elif snap.status == "capped" and snap.error_message:
        console.print(f"[dim]{escape(snap.error_message)}[/dim]")


@app.command()
def browser() -> None:
    """Open the AdScout browser profile on the Ad Library (sign in once, then close it)."""
    _refuse_demo_folder()
    from adscout.sources.meta_web.driver import open_until_closed
    from adscout.sources.meta_web.urls import LIBRARY_URL

    console = _console()
    console.print(
        "Opening the Ad Library in the AdScout browser profile. "
        "Sign in or complete any check yourself, then close the window to finish."
    )
    try:
        with suppress(KeyboardInterrupt):
            asyncio.run(open_until_closed(get_settings().browser_profile_dir, LIBRARY_URL))
    except ProfileBusy as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from None
    console.print("Browser closed.")


def _downloader(media_dir: Path) -> "MediaDownloader":
    from adscout.services.media import MediaDownloader

    return MediaDownloader(media_dir)


@app.command()
def media(
    snapshot_id: Annotated[int, typer.Argument(help="Snapshot id (see: adscout snapshots).")],
) -> None:
    """Download a snapshot's images and videos into the media folder (skips what's saved)."""
    _refuse_demo_folder()
    console = _console()
    settings = get_settings()
    engine = get_engine()
    with Session(engine) as session:
        if session.get(Snapshot, snapshot_id) is None:
            console.print(
                escape(f"Snapshot {snapshot_id} not found. Run: uv run adscout snapshots")
            )
            raise typer.Exit(1)
    counts = {"done": 0, "total": 0}
    cancel = CancelToken()
    try:
        with (
            _ctrl_c_cancels(cancel),
            console.status("Downloaded 0/0") as status,
        ):

            def on_progress(done: int, total: int) -> None:
                counts.update(done=done, total=total)
                status.update(f"Downloaded {done}/{total}")

            saved = asyncio.run(
                _downloader(settings.media_dir).download_snapshot(
                    engine, snapshot_id, on_progress, cancel
                )
            )
    except KeyboardInterrupt:
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(130) from None

    done, total = counts["done"], counts["total"]
    if total == 0:
        console.print(f"Snapshot {snapshot_id} has no media to download.")
        return
    console.print(f"Downloaded {done}/{total}")
    note = " Stopped early." if cancel.cancelled else ""
    console.print(
        f"Saved {saved} of {total} media files for snapshot {snapshot_id}"
        f" ({done - saved} skipped).{note}"
    )


DEFAULT_PORT = 8787
DEMO_SEED = 7


def _log_to_console() -> None:
    """Show AdScout's own INFO logs (scheduler, searches) on stderr while serving."""
    log = logging.getLogger("adscout")
    if any(getattr(h, "adscout_console", False) for h in log.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.adscout_console = True  # type: ignore[attr-defined]
    log.addHandler(handler)
    log.setLevel(logging.INFO)


STATIC_DIR = Path(__file__).parent / "static"


def _open_when_up(url: str, port: int, timeout: float = 15.0) -> None:
    """Open ``url`` in the default browser once the server accepts connections."""

    def wait_then_open() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            return
        webbrowser.open(url)

    threading.Thread(target=wait_then_open, name="adscout-open", daemon=True).start()


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port to listen on (localhost only).")] = DEFAULT_PORT,
    no_open: Annotated[bool, typer.Option("--no-open", help="Don't open the browser.")] = False,
) -> None:
    """Start the AdScout GUI at http://127.0.0.1:PORT and open it in the browser.

    Only one server runs per data folder: a second ``serve`` opens the running one instead.
    """
    _refuse_demo_folder(serving=True)
    _serve(get_settings(), port=port, no_open=no_open, demo=False)


@app.command()
def demo(
    reset: Annotated[
        bool,
        typer.Option(
            "--reset",
            help="Rebuild the sample data from scratch (wipes demo edits; data older than "
            "7 days is rebuilt anyway).",
        ),
    ] = False,
    port: Annotated[int, typer.Option(help="Port to listen on (localhost only).")] = DEMO_PORT,
    no_open: Annotated[bool, typer.Option("--no-open", help="Don't open the browser.")] = False,
) -> None:
    """Explore AdScout with invented sample data (never touches Meta or Claude).

    The demo lives in ./data-demo with its own server lock and port, so it can run beside
    the real app. Searches, checks and analyses are switched off. --reset, or sample data
    older than 7 days, rebuilds the folder and wipes any demo edits (exclusions, settings).
    """
    settings = get_settings().model_copy(update={"data_dir": DEMO_DATA_DIR})
    _serve(settings, port=port, no_open=no_open, demo=True, reset=reset)


def _prepare_demo(console: Console, data_dir: Path, *, reset: bool) -> None:
    from adscout.demo import generator

    try:
        if reset:
            generator.reset_demo(data_dir)
        if not generator.is_current(data_dir, DEMO_SEED, _now()):
            console.print("Building the sample data (about 10 seconds)...")
        generator.generate_demo(data_dir, DEMO_SEED)
    except generator.NotADemoFolder as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


def _serve(
    settings: Settings, *, port: int, no_open: bool, demo: bool, reset: bool = False
) -> None:
    import uvicorn
    from filelock import Timeout

    from adscout.api.app import BUILD_HINT, create_app
    from adscout.api.deps import build_watch_scheduler

    console = _console()
    # Before create_app (and its startup sweep of "running" snapshots, which would
    # otherwise close the live searches of the server that is already running).
    lock = server_lock(settings.data_dir)
    try:
        lock.acquire()
    except Timeout:
        running = read_server_port(settings.data_dir, port)
        typer.echo(single_instance_message(running))
        if not no_open:
            webbrowser.open(f"http://127.0.0.1:{running}")
        raise typer.Exit(0) from None
    try:
        write_server_port(settings.data_dir, port)
        if not (STATIC_DIR / "index.html").is_file():
            console.print(f"[yellow]{escape(BUILD_HINT)}[/yellow]")
        if demo:
            _prepare_demo(console, settings.data_dir, reset=reset)
        _log_to_console()
        # The daily watchlist runs only here, in the one real server that holds the lock.
        application = create_app(
            settings,
            static_dir=STATIC_DIR,
            watch_scheduler=None if demo else build_watch_scheduler,
            port=port,
            demo=demo,
        )
        url = f"http://127.0.0.1:{port}"
        name = "The AdScout demo" if demo else "AdScout"
        console.print(f"{name} is running at {url} (Ctrl+C to stop)")
        if not no_open:
            _open_when_up(url, port)
        uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning")
    finally:
        lock.release()


@app.command()
def snapshots(
    keyword: Annotated[str | None, typer.Argument(help="Only runs for this keyword.")] = None,
) -> None:
    """List past scrape runs, newest first."""
    console = _console()
    tz = ZoneInfo(get_settings().timezone)
    with Session(get_engine()) as session:
        rows = list_snapshots(session, keyword)
    if not rows:
        console.print('No snapshots yet. Run: uv run adscout scrape "keyword"')
        return
    table = Table(caption=escape(f"Times in {tz.key}"), pad_edge=False)
    for name in ("ID", "Keyword", "Cty", "Status", "Ads", "Advs", "Started", "Took"):
        table.add_column(
            name,
            justify="right" if name in ("ID", "Ads", "Advs") else "left",
            no_wrap=True,
            overflow="ellipsis",
            max_width=30 if name == "Keyword" else None,
        )
    for s in rows:
        took = ""
        if s.finished_at is not None:
            took = f"{int((s.finished_at - s.started_at).total_seconds())}s"
        table.add_row(
            str(s.id),
            escape(s.query),
            escape(s.country),
            escape(s.status),
            str(s.ad_count),
            str(s.advertiser_count),
            _local(s.started_at, tz),
            took,
        )
    console.print(table)


PROVISIONAL_BADGE = "PROVISIONAL — angle analysis not run yet"
_PARTIAL_STATUSES = ("capped", "blocked", "cancelled")
_CHIPS = {
    "pass": "[bold black on green] PASS [/]",
    "warn": "[bold black on yellow] WARN [/]",
    "fail": "[bold white on red] FAIL [/]",
    "pending": "[dim] PENDING [/]",
}


_STATUS_COLOURS = {"pass": "green", "warn": "yellow", "fail": "red", "pending": "dim"}
_LEADER_LABELS = {
    "longevity": "Longevity",
    "continuity": "Continuity",
    "volume": "Volume",
    "angles": "Angles",
}


class _Glyphs(NamedTuple):
    spark: str
    bar: str
    warn: str
    ge: str


_UNICODE_GLYPHS = _Glyphs(spark="▁▂▃▄▅▆▇█", bar="█", warn="▲", ge="≥")
_ASCII_GLYPHS = _Glyphs(spark="_.-:=+*#", bar="#", warn="!", ge=">=")


def _glyphs(console: Console) -> _Glyphs:
    """Decorative glyphs (gauge, sparkline, warning, ≥), ASCII if the console lacks them."""
    try:
        "".join(_UNICODE_GLYPHS).encode(console.encoding)
    except (UnicodeEncodeError, LookupError):
        return _ASCII_GLYPHS
    return _UNICODE_GLYPHS


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _score_colour(total: int) -> str:
    return "green" if total >= 70 else "yellow" if total >= 40 else "red"


def _gauge(total: int, bar: str, width: int = 20) -> str:
    filled = round(total * width / 100)
    return f"{bar * filled}[dim]{'·' * (width - filled)}[/dim]"


def _points(f: FactorResult) -> str:
    earned = "—" if f.points is None else f"{f.points:.1f}"
    return f"{earned}/{f.max_points}"


def _sparkline(buckets: list[TimelineBucket], spark: str) -> str:
    peak = max(b.new_creatives for b in buckets)
    cells: list[str] = []
    for b in buckets:
        if b.new_creatives == 0 or peak == 0:
            cells.append(f"[dim]{spark[0]}[/dim]")
        else:
            idx = max(1, round(b.new_creatives / peak * (len(spark) - 1)))
            cells.append(f"[cyan]{spark[idx]}[/cyan]")
    return "".join(cells)


def _print_report(
    console: Console,
    rep: Report,
    tz: ZoneInfo,
) -> None:
    snap = rep.snapshot
    score: WinnerScore = rep.score
    buckets: list[TimelineBucket] = list(rep.timeline_month)
    first_seen: dict[str, int] = {}
    for ad in rep.ads:
        if ad.advertiser_key is not None:
            first_seen.setdefault(ad.advertiser_key, len(first_seen))
    top: list[AdvertiserRow] = sorted(
        (a for a in rep.advertisers if a.ads > 0 and not a.excluded),
        key=lambda a: (-a.ads, first_seen.get(a.key, len(first_seen))),
    )[:5]
    glyphs = _glyphs(console)
    zone = snap.started_at.astimezone(tz).strftime("%Z")
    query = escape(f'"{snap.query}"')
    console.print(
        f"[bold reverse] ADSCOUT [/] [dim]report #{snap.id}[/dim]  [bold]{query}[/bold]"
        f" [dim]·[/dim] {escape(snap.country)}"
        f" [dim]·[/dim] {_local(snap.started_at, tz)} {escape(zone)}"
        f" [dim]·[/dim] {escape(snap.status)} [dim]·[/dim] {snap.ad_count} ads"
    )
    console.print()
    lead = score.leader
    colour = _score_colour(score.headline)
    gauge = _gauge(score.headline, glyphs.bar)
    line = f"[bold {colour}]Winner Score {score.headline}/100[/]  [{colour}]{gauge}[/]"
    if score.provisional:
        line += f"  [bold black on yellow] {escape(PROVISIONAL_BADGE)} [/]"
    console.print(line)
    if lead is not None:
        console.print(
            f"[bold]{escape(lead.page_name)}[/bold] [dim]·[/dim] {lead.ads} ads"
            f" [dim]·[/dim] {lead.creatives} creatives"
        )
    console.print(
        f"[dim]Market demand[/dim] [bold]{score.total}/100[/bold] [dim]·[/dim]"
        f" {score.advertisers} advertisers [dim]({escape(score.saturation)})[/dim]"
    )
    console.print(
        f"[dim]relevant[/dim] {score.relevant_ads}  [dim]excluded[/dim] {score.excluded_ads}"
        f"  [dim]creatives[/dim] {score.creatives}"
    )
    console.print()

    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
    table.add_column("Market factors", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    table.add_column("Fail <", justify="right", no_wrap=True)
    table.add_column(f"Pass {glyphs.ge}", justify="right", no_wrap=True)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Points", justify="right", no_wrap=True)
    for f in score.factors:
        table.add_row(
            escape(f.label),
            escape(f.display),
            _num(f.threshold.fail),
            _num(f.threshold.pass_),
            _CHIPS[f.status],
            _points(f),
            style="dim" if f.status == "pending" else None,
        )
    console.print(table)
    width = max(len(f.label) for f in score.factors)
    for f in score.factors:
        detail = f.detail.replace("≥", glyphs.ge)
        console.print(f"  [dim]{escape(f.label.ljust(width))}  {escape(detail)}[/dim]")
    if lead is not None:
        chips = " [dim]·[/dim] ".join(
            f"{escape(_LEADER_LABELS.get(f.key, f.label))} [{_STATUS_COLOURS[f.status]}]"
            f"{f.status.upper()}[/]"
            for f in lead.factors
        )
        console.print(f"[bold]{escape(lead.page_name)}[/bold]: {chips}")

    partial = snap.status in _PARTIAL_STATUSES
    if score.warnings or partial:
        console.print()
    for warning in score.warnings:
        console.print(f"[yellow]{glyphs.warn} {escape(warning)}[/yellow]")
    if partial:
        note = f"Partial data: this snapshot stopped early ({snap.status})."
        console.print(f"[yellow]! {escape(note)}[/yellow]")

    if buckets:
        peak = max(b.new_creatives for b in buckets)
        console.print()
        console.print(
            f"[bold dim]NEW CREATIVES / MONTH[/]  {buckets[0].start:%Y-%m} "
            f"{_sparkline(buckets, glyphs.spark)} {buckets[-1].start:%Y-%m}  [dim]peak {peak}[/dim]"
        )

    if top:
        console.print()
        adv = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
        adv.add_column("Top advertisers", no_wrap=True, overflow="ellipsis", max_width=40)
        adv.add_column("Page", style="dim", no_wrap=True, overflow="ellipsis", max_width=24)
        adv.add_column("Ads", justify="right")
        adv.add_column("Share", justify="right", style="dim")
        for a in top:
            share = a.ads / score.relevant_ads if score.relevant_ads else 0.0
            adv.add_row(escape(a.name), escape(a.key), str(a.ads), f"{share:.0%}")
        console.print(adv)


@app.command()
def report(
    snapshot_id: Annotated[int, typer.Argument(help="Snapshot id (see: adscout snapshots).")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the Winner Score as JSON only.")
    ] = False,
) -> None:
    """Score a snapshot: Winner Score, factors, timeline and top advertisers."""
    console = _console()
    tz = ZoneInfo(get_settings().timezone)
    with Session(get_engine()) as session:
        try:
            rep = build_report(session, snapshot_id, _now(), tz)
        except LookupError:
            console.print(
                escape(f"Snapshot {snapshot_id} not found. Run: uv run adscout snapshots")
            )
            raise typer.Exit(1) from None
        except ValueError as exc:
            console.print(f"[red]Cannot score snapshot {snapshot_id}: {escape(str(exc))}[/red]")
            raise typer.Exit(1) from None
    if as_json:
        # Bytes: JSON is UTF-8 whatever the console encoding (cp1252 pipes on Windows).
        typer.echo(rep.score.model_dump_json(indent=2, by_alias=True).encode())
        return
    _print_report(console, rep, tz)


@app.command()
def exclude(
    keyword: Annotated[
        str | None,
        typer.Argument(help="Keyword the override applies to (or use --snapshot)."),
    ] = None,
    snapshot_id: Annotated[
        int | None,
        typer.Option(
            "--snapshot",
            metavar="ID",
            help="Snapshot whose search the override applies to (preferred; required for "
            "advertiser searches).",
        ),
    ] = None,
    ad: Annotated[str | None, typer.Option("--ad", help="Ad archive id.")] = None,
    advertiser: Annotated[
        str | None, typer.Option("--advertiser", help="Advertiser page id.")
    ] = None,
    include: Annotated[
        bool, typer.Option("--include", help="Force the target to count as relevant.")
    ] = False,
    clear: Annotated[bool, typer.Option("--clear", help="Remove the stored override.")] = False,
) -> None:
    """Exclude (or --include) an ad or advertiser from a search's Winner Score."""
    if (keyword is None) == (snapshot_id is None):
        raise typer.BadParameter("give a keyword or --snapshot ID (not both).")
    if (ad is None) == (advertiser is None):
        raise typer.BadParameter("give exactly one of --ad or --advertiser.")
    if include and clear:
        raise typer.BadParameter("--include and --clear cannot be combined.")
    scope: Literal["ad", "advertiser"] = "ad" if ad is not None else "advertiser"
    target = ad if ad is not None else advertiser
    assert target is not None
    console = _console()
    unknown = False
    try:
        with Session(get_engine()) as session:
            if snapshot_id is not None:
                snap = session.get(Snapshot, snapshot_id)
                if snap is None:
                    console.print(
                        escape(f"Snapshot {snapshot_id} not found. Run: uv run adscout snapshots")
                    )
                    raise typer.Exit(1)
                key = override_key(snap)
                subject = escape(f'{scope} {target} for snapshot #{snapshot_id} ("{snap.query}").')
            else:
                assert keyword is not None
                if keyword.strip().startswith(PAGE_KEY_PREFIX):
                    raise typer.BadParameter("use --snapshot ID for an advertiser search.")
                key = keyword
                subject = escape(f'{scope} {target} for "{keyword}".')
            if clear:
                if clear_override(session, key, scope, target):
                    console.print(f"Cleared {subject}")
                else:
                    console.print(f"[dim]No override to clear: {subject}[/dim]")
                return
            set_override(session, key, scope, target, "include" if include else "exclude")
            if keyword is not None:
                unknown = require_keyword(keyword) not in snapshot_keywords(session)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    console.print(f"{'Included' if include else 'Excluded'} {subject}")
    if unknown:
        console.print(
            "[yellow]"
            + escape(
                f'No saved search uses "{keyword}" yet; the override will apply once you search it.'
            )
            + "[/yellow]"
        )


_BACKEND_LABELS: dict[BackendName, str] = {"cli": "Claude Code", "api": "the Anthropic API"}


def _readable(token: str) -> str:
    return token.replace("_", " ")


def _print_analysis(console: Console, view: AnalysisView) -> None:
    result = view.result
    console.print(f"[bold]Verdict[/bold]  {escape(result.verdict)}")
    console.print()
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
    table.add_column("Angle")
    table.add_column("Emotion", style="dim", no_wrap=True)
    table.add_column("Creatives", justify="right")
    for a in view.angles:
        table.add_row(escape(a.name), escape(_readable(a.emotion)), str(a.creatives))
    console.print(table)
    if result.offers:
        console.print("[bold dim]OFFERS[/]")
        for o in result.offers:
            console.print(f"  {escape(o.text)} [dim]({escape(_readable(o.kind))})[/dim]")
    note = f"{_readable(result.seasonality).capitalize()}: {result.seasonality_note}".rstrip(": ")
    console.print(f"[dim]Seasonality[/dim]  {escape(note)}")


@app.command()
def analyze(
    snapshot_id: Annotated[int, typer.Argument(help="Snapshot id (see: adscout snapshots).")],
    backend: Annotated[
        BackendChoice | None,
        typer.Option(
            help="cli: Claude Code (your subscription). api: ANTHROPIC_API_KEY. "
            "auto: the API if a key is set, else Claude Code. Default: your Settings."
        ),
    ] = None,
    model: Annotated[
        ModelChoice | None, typer.Option(help="Claude model (default: your Settings).")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-run even if a cached analysis exists.")
    ] = False,
) -> None:
    """Ask Claude for the snapshot's selling angles, hooks and offers (scores Angle diversity)."""
    _refuse_demo_folder()
    console = _console()
    settings = effective_settings()
    which = backend.value if backend is not None else None
    model_id = model.value if model is not None else None
    analyzer = resolve_analyzer(settings, which, model_id)
    if analyzer is None:
        picked = (
            settings if which is None else settings.model_copy(update={"analysis_backend": which})
        )
        reason = analysis_status(picked).reason or "Angle analysis is unavailable."
        console.print(f"[red]{escape(reason)}[/red]")
        raise typer.Exit(1)

    engine = get_engine()
    now = _now()
    tz = ZoneInfo(settings.timezone)
    with Session(engine) as session:
        try:
            _, items = analysis_input(session, snapshot_id)
        except LookupError:
            console.print(
                escape(f"Snapshot {snapshot_id} not found. Run: uv run adscout snapshots")
            )
            raise typer.Exit(1) from None
        cached = (
            None
            if force or not items
            else get_analysis(
                session, snapshot_id, PROMPT_VERSION, analyzer.model, input_hash(items)
            )
        )
    if not items:
        console.print(f"[yellow]{escape(NO_RELEVANT)}[/yellow]")
        raise typer.Exit(1)

    if cached is not None:
        row = cached
        console.print(
            f"[dim]Cached analysis from {_local(row.created_at, tz)} "
            f"({escape(row.model)}). Use --force to re-run.[/dim]"
        )
    else:
        label = _BACKEND_LABELS.get(analyzer.name, analyzer.name)
        console.print(escape(f"Sending {len(items)} creatives to {label} ({analyzer.model})."))
        try:
            with console.status(escape(f"Analyzing {len(items)} creatives…")):
                row = asyncio.run(run_analysis(engine, snapshot_id, analyzer, now, force=force))
        except AnalysisError as exc:
            console.print(f"[red]{escape(exc.message)}[/red]")
            raise typer.Exit(1) from None
        except NoRelevantAds as exc:
            console.print(f"[yellow]{escape(str(exc))}[/yellow]")
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(130) from None

    with Session(engine) as session:
        rep = build_report(session, snapshot_id, now, tz, analysis=row)
    view = rep.analysis
    assert view is not None  # saved from a validated, cleaned result
    _print_analysis(console, view)
    if view.flagged_off_topic:
        glyphs = _glyphs(console)
        console.print()
        console.print(
            f"[yellow]{glyphs.warn} {escape(off_topic_warning(view.flagged_off_topic))}[/yellow]"
        )
    console.print()
    console.print(f"[dim]See the updated score: uv run adscout report {snapshot_id}[/dim]")


# --- Watchlist ---------------------------------------------------------------------------

watch_app = typer.Typer(help="Watch searches daily: add, list, run, enable, disable, remove.")
app.add_typer(watch_app, name="watch")

EMPTY_WATCHLIST = "Watch a product or advertiser to track it daily."


def _watch_label(item: WatchItem) -> str:
    if item.kind == "advertiser":
        return f"{item.query} (page {item.page_id})"
    return item.query


def _missing_item(console: Console, item_id: int) -> typer.Exit:
    console.print(escape(f"Watch item {item_id} not found. Run: uv run adscout watch list"))
    return typer.Exit(1)


@watch_app.command("add")
def watch_add(
    keyword: Annotated[
        str | None, typer.Argument(help='Keyword to watch, e.g. "posture corrector".')
    ] = None,
    advertiser: Annotated[
        str | None,
        typer.Option(
            "--advertiser", metavar="PAGE_ID", help="Watch an advertiser's page (numeric id)."
        ),
    ] = None,
    name: Annotated[
        str | None, typer.Option(help="The advertiser's display name (with --advertiser).")
    ] = None,
    country: Annotated[
        str | None,
        typer.Option(help="Two-letter country code. Default: US, or ALL for --advertiser."),
    ] = None,
) -> None:
    """Watch a keyword or an advertiser: it is checked once a day while AdScout runs."""
    console = _console()
    q = _search_query(console, {}, keyword, advertiser, name, country)
    with Session(get_engine()) as session:
        item = create_watch_item(session, q)
    where = f"page {item.page_id}, {item.country}" if item.kind == "advertiser" else item.country
    console.print(escape(f"Watching {item.query} ({where}). Watch item {item.id}."))


@watch_app.command("list")
def watch_list() -> None:
    """List watch items with their last check."""
    console = _console()
    tz = ZoneInfo(effective_settings(get_settings()).timezone)
    with Session(get_engine()) as session:
        items = list_watch_items(session)
    if not items:
        console.print(EMPTY_WATCHLIST)
        return
    table = Table(caption=escape(f"Times in {tz.key}"), pad_edge=False)
    for col_name in ("ID", "Kind", "Query", "Cty", "Enabled", "Last run", "Last status"):
        query = col_name == "Query"
        # Only the query gives way on a narrow console (it wraps): the other columns and
        # every header keep their full width (80 columns fit them all).
        table.add_column(
            col_name,
            justify="right" if col_name == "ID" else "left",
            no_wrap=not query,
            overflow="fold" if query else "ellipsis",
            max_width=40 if query else None,
        )
    for item in items:
        table.add_row(
            str(item.id),
            escape(item.kind),
            escape(_watch_label(item)),
            escape(item.country),
            "yes" if item.enabled else "no",
            _local(item.last_run_at, tz) if item.last_run_at is not None else "never",
            escape(item.last_status or ""),
        )
    console.print(table)


def _set_enabled(item_id: int, enabled: bool) -> None:
    console = _console()
    with Session(get_engine()) as session:
        try:
            item = update_watch_item(session, item_id, enabled=enabled)
        except LookupError:
            raise _missing_item(console, item_id) from None
    state = "enabled" if enabled else "disabled"
    console.print(escape(f"Watch item {item_id} ({_watch_label(item)}) {state}."))


@watch_app.command("enable")
def watch_enable(item_id: Annotated[int, typer.Argument(help="Watch item id.")]) -> None:
    """Include a watch item in the daily check."""
    _set_enabled(item_id, True)


@watch_app.command("disable")
def watch_disable(item_id: Annotated[int, typer.Argument(help="Watch item id.")]) -> None:
    """Leave a watch item out of the daily check (its history is kept)."""
    _set_enabled(item_id, False)


@watch_app.command("remove")
def watch_remove(item_id: Annotated[int, typer.Argument(help="Watch item id.")]) -> None:
    """Stop watching (its snapshots stay as ordinary saved searches)."""
    console = _console()
    with Session(get_engine()) as session:
        if not delete_watch_item(session, item_id):
            raise _missing_item(console, item_id)
    console.print(f"Watch item {item_id} removed.")


class _Runner(Protocol):
    async def run_all(self, now: datetime) -> "RunSummary": ...

    async def run_one(self, item_id: int) -> "ItemRun": ...


def _watch_runner(
    settings: Settings, engine: Engine
) -> tuple[_Runner, Callable[[], Awaitable[None]]]:
    """A runner over this process's own search manager (its searches take the browser-profile
    lock like any CLI search), and a function that waits for its analyses to finish."""
    from adscout.api.deps import build_analysis_manager, build_manager
    from adscout.watch.runner import WatchRunner

    analysis = build_analysis_manager(settings, engine, _now)
    manager = build_manager(settings, engine, analysis)
    runner = WatchRunner(engine, manager, settings=lambda: effective_settings(settings))
    return runner, analysis.wait_idle


SERVER_OWNS_DAILY_RUN = "AdScout is running. Use Run now in the app, or stop the app first."


def _server_running(settings: Settings) -> bool:
    """Whether an ``adscout serve`` holds the server lock right now."""
    from filelock import Timeout

    lock = server_lock(settings.data_dir)
    try:
        lock.acquire()
    except Timeout:
        return True
    lock.release()
    return False


def _print_item_run(console: Console, run: "ItemRun") -> None:
    where = f" (snapshot {run.snapshot_id})" if run.snapshot_id is not None else ""
    console.print(escape(f"{run.query}: {run.status}{where}"))
    if run.alert:
        console.print(f"[yellow]  {escape(run.alert)}[/yellow]")


@watch_app.command("run")
def watch_run(
    item_id: Annotated[
        int | None, typer.Argument(help="Check only this item now (default: the daily run).")
    ] = None,
) -> None:
    """Check watch items now: one item, or the daily run (once per day, with polite gaps)."""
    _refuse_demo_folder()
    from adscout.watch.runner import ItemRun

    console = _console()
    settings = get_settings()
    if item_id is None and _server_running(settings):
        # The server owns the daily run (its scheduler and the shared search queue).
        console.print(escape(SERVER_OWNS_DAILY_RUN))
        raise typer.Exit(1)
    engine = get_engine()
    runner, drain = _watch_runner(settings, engine)

    async def go() -> "ItemRun | RunSummary":
        try:
            if item_id is not None:
                return await runner.run_one(item_id)
            return await runner.run_all(_now())
        finally:
            await drain()

    try:
        with console.status("Checking watch items..."):
            outcome = asyncio.run(go())
    except LookupError:
        raise _missing_item(console, item_id or 0) from None
    except KeyboardInterrupt:
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(130) from None

    if isinstance(outcome, ItemRun):
        _print_item_run(console, outcome)
        if outcome.profile_busy:
            console.print(f"[red]{escape(PROFILE_BUSY_MESSAGE)}[/red]")
            raise typer.Exit(1)
        return
    if outcome.skipped == "already_ran":
        console.print("The daily check already ran today.")
    elif outcome.skipped == "no_items":
        console.print(EMPTY_WATCHLIST)
    elif outcome.skipped == "busy":
        console.print("A daily check is already running.")
    for run in outcome.items:
        _print_item_run(console, run)
    if outcome.skipped == "profile_busy" or outcome.profile_busy:
        console.print(f"[red]{escape(PROFILE_BUSY_MESSAGE)}[/red]")
        raise typer.Exit(1)
    if outcome.blocked:
        raise typer.Exit(2)
