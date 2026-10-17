"""Single-instance server lock and the browser-profile lock."""

import asyncio
import webbrowser
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock
from typer.testing import CliRunner

import adscout.cli as cli
from adscout.cli import app
from adscout.config import Settings
from adscout.locks import (
    PROFILE_BUSY_MESSAGE,
    ProfileBusy,
    acquire_profile,
    acquire_profile_async,
    profile_lock,
    server_lock,
)
from adscout.sources import SourceError

SINGLE = "AdScout is already running at http://127.0.0.1:{port}"
PROFILE = "Another AdScout search is using the browser. Wait for it to finish, then try again."


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def served(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    import uvicorn

    calls: dict[str, list[Any]] = {"run": [], "web": [], "up": []}
    settings = Settings(_env_file=None, data_dir=data_dir)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "STATIC_DIR", data_dir / "static")
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: calls["run"].append(kw))
    monkeypatch.setattr(webbrowser, "open", lambda url: calls["web"].append(url))
    monkeypatch.setattr(cli, "_open_when_up", lambda url, port: calls["up"].append(url))
    return calls


def test_server_lock_second_instance_exits_with_copy(
    data_dir: Path, served: dict[str, list[Any]]
) -> None:
    (data_dir / "server.port").write_text("9123")
    held = server_lock(data_dir)
    held.acquire()
    try:
        result = CliRunner().invoke(app, ["serve"], env={"COLUMNS": "200"})
    finally:
        held.release()
    assert result.exit_code == 0, result.output
    assert SINGLE.format(port=9123) in result.output
    assert served["web"] == ["http://127.0.0.1:9123"]
    assert served["run"] == [] and served["up"] == []


def test_server_lock_second_instance_no_open(data_dir: Path, served: dict[str, list[Any]]) -> None:
    held = server_lock(data_dir)
    held.acquire()
    try:
        result = CliRunner().invoke(app, ["serve", "--no-open"], env={"COLUMNS": "200"})
    finally:
        held.release()
    assert result.exit_code == 0, result.output
    # No port file: fall back to the default port.
    assert SINGLE.format(port=8787) in result.output
    assert served["web"] == [] and served["run"] == []


def test_serve_holds_lock_and_writes_port(
    data_dir: Path, served: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[bool, str]] = []

    def fake_run(application: Any, **kw: Any) -> None:
        probe = FileLock(data_dir / "server.lock", timeout=0)
        try:
            probe.acquire()
        except Exception:
            seen.append((True, (data_dir / "server.port").read_text()))
        else:
            probe.release()
            seen.append((False, ""))

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(app, ["serve", "--no-open", "--port", "9555"])
    assert result.exit_code == 0, result.output
    assert seen == [(True, "9555")]
    # Released on exit: a new server can start.
    again = server_lock(data_dir)
    again.acquire()
    again.release()


def test_profile_lock_timeout_message(data_dir: Path) -> None:
    held = profile_lock(data_dir)
    acquire_profile(held)
    try:
        waiting = profile_lock(data_dir, timeout_s=0.1)
        with pytest.raises(ProfileBusy) as info:
            acquire_profile(waiting)
        assert str(info.value) == PROFILE == PROFILE_BUSY_MESSAGE
        assert isinstance(info.value, SourceError)
        with pytest.raises(ProfileBusy):
            asyncio.run(acquire_profile_async(waiting))
    finally:
        held.release()
    acquire_profile(waiting)
    waiting.release()


async def test_profile_lock_async_waits_for_release(data_dir: Path) -> None:
    held = profile_lock(data_dir)
    acquire_profile(held)
    waiting = profile_lock(data_dir, timeout_s=5)
    task = asyncio.ensure_future(acquire_profile_async(waiting))
    await asyncio.sleep(0.1)
    assert not task.done()
    held.release()
    await asyncio.wait_for(task, 2)
    assert waiting.is_locked
    waiting.release()


def test_cli_browser_prints_profile_copy(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, data_dir=data_dir)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    held = profile_lock(data_dir)
    acquire_profile(held)
    monkeypatch.setattr("adscout.locks.PROFILE_TIMEOUT_S", 0.1)
    try:
        result = CliRunner().invoke(app, ["browser"], env={"COLUMNS": "200"})
    finally:
        held.release()
    assert result.exit_code == 1, result.output
    assert PROFILE in result.output
