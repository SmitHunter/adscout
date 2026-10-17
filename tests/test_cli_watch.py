"""``adscout watch ...`` commands, the scheduler's place in ``serve``, and notifications."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session
from typer.testing import CliRunner

import adscout.cli as cli
from adscout.api.app import create_app
from adscout.api.deps import ApiState
from adscout.cli import app
from adscout.config import Settings
from adscout.db.repo import list_watch_items, make_engine
from adscout.locks import server_lock
from adscout.watch import notify
from adscout.watch.runner import ItemRun, RunSummary
from adscout.watch.scheduler import WatchScheduler


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = Settings(_env_file=None, data_dir=tmp_path / "data")
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    return s


@pytest.fixture
def engine(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = make_engine(settings.db_path)
    monkeypatch.setattr(cli, "get_engine", lambda: eng)
    return eng


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "200"})
    return result.exit_code, result.output


def test_cli_watch_add_list_remove(engine: Engine) -> None:
    code, out = _invoke("watch", "list")
    assert code == 0, out
    assert "Watch a product or advertiser to track it daily." in out

    code, out = _invoke("watch", "add", "posture corrector", "--country", "gb")
    assert code == 0, out
    assert "Watching posture corrector (GB)" in out
    code, out = _invoke("watch", "add", "--advertiser", "12345", "--name", "Alpha Store")
    assert code == 0, out
    # Adding the same search again keeps one item.
    _invoke("watch", "add", "posture corrector", "--country", "GB")
    with Session(engine) as s:
        items = list_watch_items(s)
    assert [(i.kind, i.query, i.page_id, i.country) for i in items] == [
        ("keyword", "posture corrector", None, "GB"),
        ("advertiser", "Alpha Store", "12345", "ALL"),
    ]
    first, second = (i.id for i in items)

    code, out = _invoke("watch", "disable", str(second))
    assert code == 0, out
    code, out = _invoke("watch", "list")
    assert code == 0, out
    assert "posture corrector" in out and "Alpha Store" in out and "advertiser" in out
    assert "never" in out
    with Session(engine) as s:
        assert [i.enabled for i in list_watch_items(s)] == [True, False]
    _invoke("watch", "enable", str(second))
    with Session(engine) as s:
        assert [i.enabled for i in list_watch_items(s)] == [True, True]

    code, out = _invoke("watch", "remove", str(first))
    assert code == 0, out
    with Session(engine) as s:
        assert [i.id for i in list_watch_items(s)] == [second]
    code, out = _invoke("watch", "remove", "999")
    assert code == 1 and "Watch item 999 not found" in out


def test_cli_watch_add_needs_keyword_or_advertiser(engine: Engine) -> None:
    code, out = _invoke("watch", "add")
    assert code == 1 and "Give a keyword, or --advertiser PAGE_ID." in out
    code, out = _invoke("watch", "add", "x", "--country", "123")
    assert code == 1 and "Invalid country" in out


def test_cli_watch_run_uses_runner(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    calls: list[Any] = []

    class FakeRunner:
        async def run_all(self, at: datetime) -> RunSummary:
            calls.append("all")
            return RunSummary(
                started_at=now,
                finished_at=now,
                items=[
                    ItemRun(
                        watch_item_id=1,
                        query="posture corrector",
                        snapshot_id=7,
                        status="ok",
                        alert="posture corrector: active ads up 50% (10 → 15)",
                    )
                ],
            )

        async def run_one(self, item_id: int) -> ItemRun:
            calls.append(item_id)
            if item_id != 1:
                raise LookupError(item_id)
            return ItemRun(watch_item_id=1, query="posture corrector", snapshot_id=8, status="ok")

    async def fake_drain() -> None:
        calls.append("drained")

    monkeypatch.setattr(cli, "_watch_runner", lambda settings, engine: (FakeRunner(), fake_drain))
    code, out = _invoke("watch", "run")
    assert code == 0, out
    assert "posture corrector" in out and "up 50%" in out
    code, out = _invoke("watch", "run", "1")
    assert code == 0, out
    assert "snapshot 8" in out
    code, out = _invoke("watch", "run", "2")
    assert code == 1 and "Watch item 2 not found" in out
    assert calls == ["all", "drained", 1, "drained", 2, "drained"]


# --- The scheduler only runs inside ``serve`` -------------------------------------------------


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[WatchScheduler]:
    out: list[WatchScheduler] = []

    async def fake_start(self: WatchScheduler) -> None:
        out.append(self)

    async def fake_shutdown(self: WatchScheduler) -> None:
        return None

    monkeypatch.setattr(WatchScheduler, "start", fake_start)
    monkeypatch.setattr(WatchScheduler, "shutdown", fake_shutdown)
    return out


async def _run_lifespan(application: Any) -> None:
    async with application.router.lifespan_context(application):
        pass


async def test_scheduler_not_started_without_server_lock(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    started: list[WatchScheduler],
    tmp_path: Path,
) -> None:
    import uvicorn

    apps: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: apps.append(application))
    monkeypatch.setattr(cli, "STATIC_DIR", tmp_path / "static")

    # Another server holds the lock: this one exits before building anything.
    held = server_lock(settings.data_dir)
    held.acquire()
    try:
        code, out = _invoke("serve", "--no-open")
    finally:
        held.release()
    assert code == 0 and "already running" in out
    assert apps == [] and started == []

    # The app built by tests (or anything but ``serve``) never starts the scheduler.
    plain = create_app(settings)
    await _run_lifespan(plain)
    assert started == []
    assert plain.state.api.scheduler is None

    # ``serve`` with the lock held by itself starts it in the app's lifespan.
    code, out = _invoke("serve", "--no-open")
    assert code == 0, out
    [served] = apps
    await _run_lifespan(served)
    state: ApiState = served.state.api
    assert started == [state.scheduler]


# --- Desktop notifications ------------------------------------------------------------------


async def test_notify_noop_without_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "desktop_notifier", None)  # import fails
    assert notify.available() is False
    await notify.notify("AdScout", "hello")  # no error


async def test_notify_sends_and_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    sent: list[tuple[str, str]] = []

    class Good:
        def __init__(self, app_name: str) -> None:
            assert app_name == "AdScout"

        async def send(self, title: str, message: str) -> None:
            sent.append((title, message))

    class Bad(Good):
        async def send(self, title: str, message: str) -> None:
            raise RuntimeError("no notification daemon")

    module = types.ModuleType("desktop_notifier")
    module.DesktopNotifier = Good  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "desktop_notifier", module)
    assert notify.available() is True
    await notify.notify("AdScout", "posture corrector: active ads up 40% (10 → 14)")
    assert sent == [("AdScout", "posture corrector: active ads up 40% (10 → 14)")]

    module.DesktopNotifier = Bad  # type: ignore[attr-defined]
    await notify.notify("AdScout", "boom")  # swallowed


# --- The daily run belongs to the server; a busy profile; Windows consoles ----------------


def _no_runner(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    class FakeRunner:
        async def run_all(self, at: datetime) -> RunSummary:
            calls.append("all")
            return RunSummary(started_at=at, finished_at=at)

        async def run_one(self, item_id: int) -> ItemRun:
            calls.append(f"one {item_id}")
            return ItemRun(
                watch_item_id=item_id, query="posture corrector", snapshot_id=3, status="ok"
            )

    async def drain() -> None:
        return None

    monkeypatch.setattr(cli, "_watch_runner", lambda settings, engine: (FakeRunner(), drain))
    return calls


def test_cli_daily_run_refused_while_server_runs(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _no_runner(monkeypatch)
    lock = server_lock(settings.data_dir)
    lock.acquire()
    try:
        code, out = _invoke("watch", "run")
        assert code == 1
        assert "AdScout is running. Use Run now in the app, or stop the app first." in out
        assert calls == []
        # One item still runs: it takes the browser-profile lock like any CLI search.
        code, out = _invoke("watch", "run", "1")
        assert code == 0, out
        assert calls == ["one 1"]
    finally:
        lock.release()
    code, out = _invoke("watch", "run")
    assert code == 0, out
    assert calls == ["one 1", "all"]


@pytest.mark.parametrize("busy", ["skipped", "mid-run"])
def test_cli_daily_run_profile_busy_exit_1(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, busy: str
) -> None:
    from adscout.locks import PROFILE_BUSY_MESSAGE

    now = datetime(2026, 10, 15, tzinfo=UTC)

    class BusyRunner:
        async def run_all(self, at: datetime) -> RunSummary:
            if busy == "skipped":
                return RunSummary(started_at=now, finished_at=now, skipped="profile_busy")
            item = ItemRun(watch_item_id=1, query="neck pillow", snapshot_id=4, status="error")
            return RunSummary(started_at=now, finished_at=now, items=[item], profile_busy=True)

        async def run_one(self, item_id: int) -> ItemRun:
            raise AssertionError

    async def drain() -> None:
        return None

    monkeypatch.setattr(cli, "_watch_runner", lambda settings, engine: (BusyRunner(), drain))
    code, out = _invoke("watch", "run")
    assert code == 1
    assert PROFILE_BUSY_MESSAGE in " ".join(out.split())


def test_cli_watch_list_on_cp1252_stream(engine: Engine) -> None:
    """A cp1252 stdout (PowerShell capturing output on Windows): no U+FFFD for a UTF-8
    reader, and every header in full at the default 80 columns."""
    _invoke("watch", "add", "posture corrector with a very long keyword", "--country", "gb")
    _invoke("watch", "add", "--advertiser", "12345", "--name", "Café 店 Store 🔥")
    _invoke("watch", "disable", "1")

    result = CliRunner(charset="cp1252").invoke(app, ["watch", "list"], env={"COLUMNS": "80"})

    assert result.exception is None, result.output
    assert result.exit_code == 0
    raw = result.stdout_bytes
    assert "\ufffd" not in raw.decode("utf-8", errors="replace")
    assert "\ufffd" not in raw.decode("cp1252", errors="replace")
    out = result.output
    header = next(line for line in out.splitlines() if "Kind" in line)
    for name in ("ID", "Kind", "Query", "Cty", "Enabled", "Last run", "Last status"):
        assert name in header, header
    assert "| Cafe ? Store ?" in out  # accents keep their letter; emoji and CJK print "?"
    assert all(len(line) <= 80 for line in out.splitlines())


def test_cli_run_one_on_busy_profile_prints_busy_copy(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adscout.locks import PROFILE_BUSY_MESSAGE

    class BusyRunner:
        async def run_one(self, item_id: int) -> ItemRun:
            return ItemRun(
                watch_item_id=item_id,
                query="neck pillow",
                snapshot_id=4,
                status="error",
                profile_busy=True,
            )

    async def drain() -> None:
        return None

    monkeypatch.setattr(cli, "_watch_runner", lambda settings, engine: (BusyRunner(), drain))
    code, out = _invoke("watch", "run", "1")
    assert code == 1
    assert PROFILE_BUSY_MESSAGE in " ".join(out.split())
