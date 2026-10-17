import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import SecretStr

from adscout.analysis.backends import (
    AnalysisError,
    AnthropicApiAnalyzer,
    ClaudeCliAnalyzer,
    _looks_logged_out,
    analysis_status,
    resolve_analyzer,
)
from adscout.analysis.models import AngleAnalysis
from adscout.config import Settings

RESULT: dict[str, Any] = {
    "angles": [
        {
            "name": "Back pain relief",
            "description": "Promises relief from desk-job back pain.",
            "emotion": "pain_relief",
            "ad_ids": ["1", "2"],
        }
    ],
    "hooks": [{"text": "Still sore after work?", "ad_ids": ["1"]}],
    "offers": [{"kind": "discount", "text": "50% off today", "ad_ids": ["2"]}],
    "not_product_ad_ids": [],
    "seasonality": "evergreen",
    "seasonality_note": "No seasonal cues.",
    "verdict": "One. Two. Three.",
}

FAKE_CLAUDE = r"""
import json, os, pathlib, sys, time
here = pathlib.Path(__file__).parent
plan = json.loads((here / "plan.json").read_text())
counter = here / "calls.txt"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
(here / f"stdin{n}.txt").write_bytes(sys.stdin.buffer.read())
(here / f"argv{n}.json").write_text(json.dumps(sys.argv[1:]))
(here / f"env{n}.json").write_text(json.dumps(sorted(os.environ)))
step = plan[min(n, len(plan) - 1)]
time.sleep(step.get("sleep", 0))
sys.stdout.write(step.get("stdout", ""))
sys.stderr.write(step.get("stderr", ""))
sys.exit(step.get("exit", 0))
"""


def _fake_cli(tmp_path: Path, plan: list[dict[str, Any]]) -> list[str]:
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE)
    return [sys.executable, str(script)]


def _envelope(**extra: Any) -> str:
    env: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    env.update(extra)
    return json.dumps(env)


async def test_cli_parses_structured_output(tmp_path: Path) -> None:
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(structured_output=RESULT)}])
    analyzer = ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe)
    result, usage = await analyzer.analyze("SYS", "USER")
    assert result == AngleAnalysis.model_validate(RESULT)
    assert usage == {"input_tokens": 100, "output_tokens": 50}
    argv = json.loads((tmp_path / "argv0.json").read_text())
    assert argv[:2] == ["-p", "--output-format"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert argv[argv.index("--tools") + 1] == ""
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["title"] == "AngleAnalysis"
    assert "--no-session-persistence" in argv
    assert "--strict-mcp-config" in argv
    # The user's own settings and hooks never apply to the analysis.
    assert argv[argv.index("--setting-sources") + 1] == ""


async def test_cli_falls_back_to_result_string(tmp_path: Path) -> None:
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(result=json.dumps(RESULT))}])
    result, _ = await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", "U")
    assert result.verdict == "One. Two. Three."


async def test_cli_retries_once_on_bad_json_then_errors(tmp_path: Path) -> None:
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(result="not json at all")}])
    analyzer = ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe)
    with pytest.raises(AnalysisError) as err:
        await analyzer.analyze("S", "U")
    assert err.value.message == "Claude returned an unexpected format."
    assert (tmp_path / "calls.txt").read_text() == "2"


async def test_cli_retry_recovers(tmp_path: Path) -> None:
    bad = dict(RESULT, seasonality="sometimes")
    exe = _fake_cli(
        tmp_path,
        [
            {"stdout": _envelope(structured_output=bad)},
            {"stdout": _envelope(structured_output=RESULT)},
        ],
    )
    result, _ = await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", "U")
    assert result.seasonality == "evergreen"
    assert (tmp_path / "calls.txt").read_text() == "2"


async def test_cli_not_logged_in_message(tmp_path: Path) -> None:
    stdout = _envelope(is_error=True, subtype="error", result="Not logged in · Please run /login")
    exe = _fake_cli(tmp_path, [{"stdout": stdout, "exit": 1}])
    with pytest.raises(AnalysisError) as err:
        await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", "U")
    assert err.value.message == (
        "Claude Code isn't logged in. Run claude once in a terminal to sign in."
    )


async def test_cli_nonzero_exit_reports_trimmed_stderr(tmp_path: Path) -> None:
    exe = _fake_cli(tmp_path, [{"stderr": "x" * 1000 + "boom happened", "exit": 2}])
    with pytest.raises(AnalysisError) as err:
        await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", "U")
    assert err.value.message.endswith("boom happened")
    assert len(err.value.message) < 400


async def test_cli_missing_executable(tmp_path: Path) -> None:
    analyzer = ClaudeCliAnalyzer("claude-sonnet-5-5", executable=str(tmp_path / "nope.exe"))
    with pytest.raises(AnalysisError) as err:
        await analyzer.analyze("S", "U")
    assert "Claude Code" in err.value.message


async def test_cli_timeout(tmp_path: Path) -> None:
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(structured_output=RESULT), "sleep": 10}])
    analyzer = ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe, timeout_s=0.5)
    with pytest.raises(AnalysisError) as err:
        await analyzer.analyze("S", "U")
    assert "timed out" in err.value.message


def test_timeout_message_default_is_five_minutes() -> None:
    from adscout.analysis.backends import _timeout_message

    assert _timeout_message(300) == "Claude Code timed out after 5 minutes."


async def test_cli_sends_user_message_on_stdin(tmp_path: Path) -> None:
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(structured_output=RESULT)}])
    user = 'Product keyword: posture corrector\n<ads>\n["é"]\n</ads>\n'
    await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", user)
    assert (tmp_path / "stdin0.txt").read_bytes() == user.encode("utf-8")
    argv = json.loads((tmp_path / "argv0.json").read_text())
    assert user not in argv


async def test_cli_env_has_no_anthropic_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-not-leak")
    monkeypatch.setenv("ADSCOUT_MARKER", "1")
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(structured_output=RESULT)}])
    await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", "U")
    names = {n.upper() for n in json.loads((tmp_path / "env0.json").read_text())}
    assert "ADSCOUT_MARKER" in names  # the rest of the environment is passed through
    assert "ANTHROPIC_API_KEY" not in names
    assert "ANTHROPIC_AUTH_TOKEN" not in names


@pytest.mark.parametrize("name", ["claude.cmd", "CLAUDE.BAT", "claude.Cmd"])
async def test_cli_rejects_npm_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    spawned: list[Any] = []

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        spawned.append(args)
        raise AssertionError("must not spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    analyzer = ClaudeCliAnalyzer("claude-sonnet-5-5", executable=str(tmp_path / name))
    with pytest.raises(AnalysisError) as err:
        await analyzer.analyze("S", "U")
    assert err.value.message == (
        "Claude Code was installed with npm. Install the native Claude Code build to use it "
        "from AdScout."
    )
    assert spawned == []


class _HangingProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.waited = False
        self.started = asyncio.Event()

    async def communicate(self, data: bytes | None = None) -> tuple[bytes, bytes]:
        self.started.set()
        await asyncio.Event().wait()  # never returns
        raise AssertionError

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        self.returncode = -9
        return -9


async def test_cli_cancellation_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _HangingProc()

    async def spawn(*args: Any, **kwargs: Any) -> _HangingProc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        ClaudeCliAnalyzer("claude-sonnet-5-5", executable="claude").analyze("S", "U")
    )
    await proc.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed and proc.waited


async def test_cli_tempdir_ignores_cleanup_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    real = tempfile.TemporaryDirectory

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", spy)
    exe = _fake_cli(tmp_path, [{"stdout": _envelope(structured_output=RESULT)}])
    await ClaudeCliAnalyzer("claude-sonnet-5-5", executable=exe).analyze("S", "U")
    assert seen and all(k.get("ignore_cleanup_errors") is True for k in seen)


# --- API backend ---------------------------------------------------------------

KEY = "sk-ant-test-SUPERSECRET"


def _message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }


def _client(
    responses: list[httpx2.Response], seen: list[httpx2.Request]
) -> anthropic.AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1]

    return anthropic.AsyncAnthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


async def test_api_parses_output() -> None:
    seen: list[httpx2.Request] = []
    client = _client([httpx2.Response(200, json=_message(json.dumps(RESULT)))], seen)
    analyzer = AnthropicApiAnalyzer("claude-sonnet-5-5", client=client)
    result, usage = await analyzer.analyze("SYS", "USER")
    assert result == AngleAnalysis.model_validate(RESULT)
    assert usage is not None and usage["input_tokens"] == 1200
    req = seen[0]
    assert req.url.path == "/v1/messages"
    body = json.loads(req.content)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 16000
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]


async def test_api_haiku_has_no_fallbacks() -> None:
    seen: list[httpx2.Request] = []
    client = _client([httpx2.Response(200, json=_message(json.dumps(RESULT)))], seen)
    await AnthropicApiAnalyzer("claude-haiku-5-5", client=client).analyze("S", "U")
    body = json.loads(seen[0].content)
    assert "fallbacks" not in body
    assert "server-side-fallback" not in seen[0].headers.get("anthropic-beta", "")


async def test_api_refusal_maps_to_error() -> None:
    seen: list[httpx2.Request] = []
    client = _client(
        [httpx2.Response(200, json=_message("I can't help with that.", "refusal"))], seen
    )
    with pytest.raises(AnalysisError) as err:
        await AnthropicApiAnalyzer("claude-sonnet-5-5", client=client).analyze("S", "U")
    assert err.value.message == "Claude declined to analyze these ads."
    assert len(seen) == 1


async def test_api_max_tokens_is_cut_off_without_retry() -> None:
    seen: list[httpx2.Request] = []
    client = _client([httpx2.Response(200, json=_message('{"angles": [', "max_tokens"))], seen)
    with pytest.raises(AnalysisError) as err:
        await AnthropicApiAnalyzer("claude-sonnet-5-5", client=client).analyze("S", "U")
    assert err.value.message == (
        "Claude's answer was cut off. Try a smaller search or a different model."
    )
    assert len(seen) == 1


async def test_api_retries_once_on_bad_json_then_errors() -> None:
    seen: list[httpx2.Request] = []
    client = _client([httpx2.Response(200, json=_message('{"angles": 3}'))], seen)
    with pytest.raises(AnalysisError) as err:
        await AnthropicApiAnalyzer("claude-sonnet-5-5", client=client).analyze("S", "U")
    assert err.value.message == "Claude returned an unexpected format."
    assert len(seen) == 2


async def test_api_auth_error_message_has_no_key() -> None:
    seen: list[httpx2.Request] = []
    body = {
        "type": "error",
        "error": {"type": "authentication_error", "message": f"invalid x-api-key {KEY}"},
    }
    client = _client([httpx2.Response(401, json=body)], seen)
    with pytest.raises(AnalysisError) as err:
        await AnthropicApiAnalyzer("claude-sonnet-5-5", client=client).analyze("S", "U")
    assert "API key" in err.value.message
    assert KEY not in err.value.message
    assert KEY not in str(err.value)
    assert err.value.__cause__ is None


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(429, "rate limit"), (529, "overloaded"), (500, "Anthropic API error (500)")],
)
async def test_api_status_errors_are_friendly(status: int, fragment: str) -> None:
    seen: list[httpx2.Request] = []
    body = {"type": "error", "error": {"type": "x", "message": "m"}}
    client = _client([httpx2.Response(status, json=body)], seen)
    with pytest.raises(AnalysisError) as err:
        await AnthropicApiAnalyzer("claude-sonnet-5-5", client=client).analyze("S", "U")
    assert fragment in err.value.message


async def test_api_connection_error_is_friendly() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    client = anthropic.AsyncAnthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(AnalysisError) as err:
        await AnthropicApiAnalyzer("claude-sonnet-5-5", client=client).analyze("S", "U")
    assert "reach the Anthropic API" in err.value.message


# --- resolution ---------------------------------------------------------------


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, **kw)


def test_resolve_auto_prefers_api_key_then_cli_then_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/claude.exe")
    with_key = _settings(tmp_path, anthropic_api_key=SecretStr(KEY))
    api = resolve_analyzer(with_key)
    assert isinstance(api, AnthropicApiAnalyzer)
    assert api.name == "api" and api.model == "claude-sonnet-5-5"

    no_key = _settings(tmp_path, anthropic_api_key=None)
    cli = resolve_analyzer(no_key)
    assert isinstance(cli, ClaudeCliAnalyzer)
    assert cli.name == "cli"
    assert analysis_status(no_key).backend == "cli"

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert resolve_analyzer(no_key) is None
    status = analysis_status(no_key)
    assert not status.available
    assert status.backend is None
    assert status.reason == (
        "Angle analysis needs Claude. Install Claude Code or set ANTHROPIC_API_KEY in .env, "
        "then restart AdScout."
    )


def test_resolve_explicit_backend_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/claude.exe")
    s = _settings(tmp_path, anthropic_api_key=SecretStr(KEY), analysis_backend="cli")
    cli = resolve_analyzer(s, model="claude-opus-5-5")
    assert isinstance(cli, ClaudeCliAnalyzer) and cli.model == "claude-opus-5-5"
    api = resolve_analyzer(s, which="api")
    assert isinstance(api, AnthropicApiAnalyzer)

    no_key = _settings(tmp_path, anthropic_api_key=None)
    assert resolve_analyzer(no_key, which="api") is None
    status = analysis_status(_settings(tmp_path, anthropic_api_key=None, analysis_backend="api"))
    assert not status.available and status.reason is not None
    assert KEY not in (status.reason or "")


@pytest.mark.parametrize(
    "text",
    [
        "Not logged in · Please run /login",
        "Invalid API key · Please run /login",
        "Error: please log in to continue",
        "OAuth token expired. Run /login.",
    ],
)
def test_logged_out_detected(text: str) -> None:
    assert _looks_logged_out(text)


@pytest.mark.parametrize(
    "text",
    [
        "The dialog interrupted the request",
        "catalog index out of range",
        "wrote https://example.test/loginfo.txt",
        "Error: model overloaded, try again",
    ],
)
def test_other_failures_are_not_mistaken_for_logged_out(text: str) -> None:
    assert not _looks_logged_out(text)
