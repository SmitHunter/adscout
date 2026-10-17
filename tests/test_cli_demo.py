"""``adscout demo``: its own data folder, lock and port, and no live actions."""

import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import adscout.cli as cli
import adscout.demo.generator as generator
from adscout.api.deps import ApiState
from adscout.cli import app
from adscout.config import Settings
from adscout.demo import DEMO_COPY, DEMO_MARKER
from adscout.locks import SERVER_PORT, server_lock

LIVE = "live path reached"


def _live(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError(LIVE)


@pytest.fixture(autouse=True)
def no_live_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every way out to Meta, Claude, a real browser or a real server raises instead.

    Hermetic even when the code under test is broken: a missing guard fails here with
    "live path reached" rather than searching or opening a window.
    """
    import uvicorn

    import adscout.analysis.backends as backends
    import adscout.api.deps as deps
    import adscout.sources.meta_web.driver as driver
    from adscout.services.media import MediaDownloader
    from adscout.sources.meta_api import MetaApiSource
    from adscout.sources.meta_web.source import MetaWebSource
    from adscout.watch.runner import WatchRunner

    for target, name in [
        (driver, "launch_context"),
        (driver, "open_until_closed"),
        (driver, "lock_profile"),
        (MetaWebSource, "search"),
        (MetaApiSource, "search"),
        (cli, "get_source"),
        (cli, "run_scrape"),
        (MediaDownloader, "download_snapshot"),
        (backends, "resolve_analyzer"),
        (cli, "resolve_analyzer"),
        (cli, "run_analysis"),
        (WatchRunner, "run_one"),
        (WatchRunner, "run_all"),
        (uvicorn, "run"),
        (webbrowser, "open"),
        (cli, "_open_when_up"),
    ]:
        monkeypatch.setattr(target, name, _live)
    # The server's search manager gets sources from this factory: none may be built.
    monkeypatch.setattr(deps, "source_factory", lambda settings: _live)


class Calls:
    def __init__(self) -> None:
        self.run: list[dict[str, Any]] = []
        self.opened: list[str] = []
        self.generated: list[tuple[Path, int]] = []
        self.reset: list[Path] = []


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Calls:
    import uvicorn

    c = Calls()
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, data_dir=Path("data"))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "STATIC_DIR", tmp_path / "static")

    def fake_generate(data_dir: Path, seed: int = 7, now: datetime | None = None) -> None:
        c.generated.append((data_dir, seed))
        data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(generator, "generate_demo", fake_generate)
    monkeypatch.setattr(generator, "reset_demo", lambda data_dir: c.reset.append(data_dir))
    monkeypatch.setattr(
        uvicorn, "run", lambda application, **kw: c.run.append({"app": application, **kw})
    )
    monkeypatch.setattr(cli, "_open_when_up", lambda url, port: c.opened.append(url))
    return c


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "200"})
    return result.exit_code, result.output


def test_demo_uses_separate_data_dir_and_lock(calls: Calls, tmp_path: Path) -> None:
    # The real app's server is running: its lock is held, yet the demo starts.
    held = server_lock(Path("data"))
    held.acquire()
    try:
        code, out = _invoke("demo", "--no-open")
    finally:
        held.release()
    assert code == 0, out
    assert "already running" not in out
    [run] = calls.run
    assert run["host"] == "127.0.0.1" and run["port"] == 8788
    state: ApiState = run["app"].state.api
    assert state.demo is True
    assert state.settings.data_dir == Path("data-demo")
    assert calls.generated == [(Path("data-demo"), 7)]
    assert calls.reset == []
    assert (tmp_path / "data-demo" / SERVER_PORT).read_text() == "8788"
    assert not (tmp_path / "data" / SERVER_PORT).exists()
    assert calls.opened == []


def test_demo_reset_port_and_open(calls: Calls) -> None:
    code, out = _invoke("demo", "--reset", "--port", "9100")
    assert code == 0, out
    assert calls.reset == [Path("data-demo")]
    assert calls.run[0]["port"] == 9100
    assert calls.opened == ["http://127.0.0.1:9100"]


def test_second_demo_opens_the_running_one(calls: Calls) -> None:
    held = server_lock(Path("data-demo"))
    held.acquire()
    try:
        (Path("data-demo") / SERVER_PORT).write_text("8790")
        code, out = _invoke("demo", "--no-open")
    finally:
        held.release()
    assert code == 0, out
    assert "http://127.0.0.1:8790" in out
    assert calls.run == [] and calls.generated == []


# --- Live commands refuse a demo folder ---------------------------------------------------


@pytest.fixture
def demo_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data-demo"
    data_dir.mkdir()
    (data_dir / DEMO_MARKER).write_text("{}")
    settings = Settings(_env_file=None, data_dir=data_dir)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return data_dir


@pytest.mark.parametrize(
    "args",
    [
        ["scrape", "posture brace"],
        ["browser"],
        ["media", "1"],
        ["analyze", "1"],
        ["watch", "run"],
        ["watch", "run", "1"],
    ],
    ids=["scrape", "browser", "media", "analyze", "watch-run", "watch-run-one"],
)
def test_live_commands_refuse_demo_folder(demo_folder: Path, args: list[str]) -> None:
    result = CliRunner().invoke(app, args, env={"COLUMNS": "200"})
    assert not isinstance(result.exception, AssertionError), result.exception
    assert result.exit_code == 1, result.output
    assert DEMO_COPY in " ".join(result.output.split())


@pytest.mark.parametrize("args", [["serve", "--no-open"], []], ids=["serve", "default"])
def test_serve_refuses_demo_folder(demo_folder: Path, args: list[str]) -> None:
    result = CliRunner().invoke(app, args, env={"COLUMNS": "200"})
    assert not isinstance(result.exception, AssertionError), result.exception
    assert result.exit_code == 1, result.output
    assert "uv run adscout demo" in " ".join(result.output.split())
    assert not (demo_folder / SERVER_PORT).exists()
