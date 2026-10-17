from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import adscout.cli as cli
from adscout.api.deps import ApiState
from adscout.cli import app
from adscout.config import Settings
from adscout.models import SearchQuery
from adscout.services.media import MediaDownloader

HINT = "Frontend not built. Run: npm --prefix web ci && npm --prefix web run build"


class Calls:
    def __init__(self) -> None:
        self.run: list[dict[str, Any]] = []
        self.opened: list[str] = []


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Calls:
    import uvicorn

    c = Calls()
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "STATIC_DIR", tmp_path / "static")

    def fake_run(application: Any, **kwargs: Any) -> None:
        c.run.append({"app": application, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(cli, "_open_when_up", lambda url, port: c.opened.append(url))
    return c


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "200"})
    return result.exit_code, result.output


def test_serve_without_static_prints_build_hint(calls: Calls) -> None:
    code, out = _invoke("serve", "--no-open")
    assert code == 0, out
    assert HINT in out
    [run] = calls.run
    assert run["host"] == "127.0.0.1" and run["port"] == 8787
    assert calls.opened == []


def test_serve_with_static_has_no_hint_and_opens_browser(calls: Calls, tmp_path: Path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<html></html>")
    code, out = _invoke("serve", "--port", "9001")
    assert code == 0, out
    assert HINT not in out
    assert "http://127.0.0.1:9001" in out
    assert calls.run[0]["port"] == 9001
    assert calls.opened == ["http://127.0.0.1:9001"]


def test_default_command_is_serve(calls: Calls) -> None:
    code, out = _invoke()
    assert code == 0, out
    assert len(calls.run) == 1
    assert calls.opened == ["http://127.0.0.1:8787"]


def test_version_still_works(calls: Calls) -> None:
    code, out = _invoke("--version")
    assert code == 0 and "0.1.0" in out
    assert calls.run == []


def test_serve_wires_media_downloader_and_source(calls: Calls) -> None:
    from adscout.sources.meta_web.source import MetaWebSource

    _invoke("serve", "--no-open")
    state: ApiState = calls.run[0]["app"].state.api
    manager = state.manager
    assert isinstance(manager._media, MediaDownloader)
    assert manager._analysis is state.analysis
    assert isinstance(manager._source_factory(SearchQuery(keyword="x")), MetaWebSource)
    with pytest.raises(ValueError, match="META_ACCESS_TOKEN"):
        manager._source_factory(SearchQuery(keyword="x", country="EU"))
