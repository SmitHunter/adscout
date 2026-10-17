"""The two ways AdScout calls Claude: the Claude Code CLI (headless, the user's subscription)
and the Anthropic API (``ANTHROPIC_API_KEY``).

Both return a schema-valid :class:`AngleAnalysis` (not yet ``clean``-ed) plus token usage, and
raise :class:`AnalysisError` with user-facing copy. Error messages never contain API keys or
raw provider error text.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import AnthropicBetaParam
from anthropic.types.beta import BetaFallbacksParam, BetaOutputConfigParam
from pydantic import BaseModel, ValidationError

from adscout.analysis.models import AngleAnalysis
from adscout.config import Settings

BackendName = Literal["cli", "api"]

UNAVAILABLE = (
    "Angle analysis needs Claude. Install Claude Code or set ANTHROPIC_API_KEY in .env, "
    "then restart AdScout."
)
NO_API_KEY = (
    "The Anthropic API backend needs ANTHROPIC_API_KEY in .env. Set it, then restart AdScout."
)
NO_CLI = "The Claude Code backend needs Claude Code installed. Install it, then restart AdScout."
NOT_LOGGED_IN = "Claude Code isn't logged in. Run claude once in a terminal to sign in."
BAD_FORMAT = "Claude returned an unexpected format."
REFUSED = "Claude declined to analyze these ads."
CUT_OFF = "Claude's answer was cut off. Try a smaller search or a different model."
NPM_SHIM = (
    "Claude Code was installed with npm. Install the native Claude Code build to use it "
    "from AdScout."
)
STDERR_TAIL = 300
# Removed from the Claude Code environment so it bills the user's subscription, not a key.
_CREDENTIAL_ENV = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})

API_MAX_TOKENS = 16000
# Server-side refusal fallbacks ("default" routing) are offered for these models on the
# Claude API; Claude Haiku 5.5 has no server-side fallback.
FALLBACK_MODELS = frozenset({"claude-sonnet-5-5", "claude-opus-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnalysisError(Exception):
    """A failed analysis. ``message`` is shown to the user as-is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _FormatError(Exception):
    """Claude's reply didn't match the schema; worth one retry."""


class Analyzer(Protocol):
    name: BackendName
    model: str

    async def analyze(
        self, system: str, user: str
    ) -> tuple[AngleAnalysis, dict[str, Any] | None]: ...


Result = tuple[AngleAnalysis, dict[str, Any] | None]


async def _with_one_retry(attempt: Callable[[], Awaitable[Result]]) -> Result:
    for tries_left in (1, 0):
        try:
            return await attempt()
        except _FormatError:
            if not tries_left:
                break
    raise AnalysisError(BAD_FORMAT)


# --- Claude Code CLI ------------------------------------------------------------------------


def _timeout_message(timeout_s: float) -> str:
    if timeout_s >= 60 and timeout_s % 60 == 0:
        minutes = int(timeout_s // 60)
        return f"Claude Code timed out after {minutes} minute{'s' if minutes != 1 else ''}."
    return f"Claude Code timed out after {timeout_s:g} seconds."


def _tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= STDERR_TAIL else "…" + text[-STDERR_TAIL:]


def _cli_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in _CREDENTIAL_ENV}


# Whole words only: "dialog in", "catalog index" or a ".../loginfo" URL are not a login.
_LOGGED_OUT = re.compile(
    r"\bnot logged in\b|\blog in\b|\binvalid api key\b|(?<![\w/.:-])/login\b",
    re.IGNORECASE,
)


def _looks_logged_out(text: str) -> bool:
    return _LOGGED_OUT.search(text) is not None


class ClaudeCliAnalyzer:
    """Runs ``claude -p`` headless with tools, MCP servers and session saving all off.

    ``executable`` may be a command prefix (e.g. ``[python, fake_claude.py]`` in tests).
    The user message goes on stdin to stay clear of Windows command-line length limits.
    """

    name: BackendName = "cli"

    def __init__(
        self,
        model: str,
        executable: str | Sequence[str] = "claude",
        timeout_s: float = 300,
    ) -> None:
        self.model = model
        self._prefix = [executable] if isinstance(executable, str) else list(executable)
        self._timeout_s = timeout_s

    def argv(self, system: str) -> list[str]:
        schema = json.dumps(AngleAnalysis.model_json_schema(), separators=(",", ":"))
        return [
            *self._prefix,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--json-schema",
            schema,
            "--system-prompt",
            system,
            "--tools",
            "",
            "--no-session-persistence",
            "--strict-mcp-config",
            # No user/project/local settings: the user's own hooks, permissions and
            # plugins stay out of a headless analysis run.
            "--setting-sources",
            "",
        ]

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        return await _with_one_retry(lambda: self._once(system, user))

    async def _run(self, system: str, user: str) -> tuple[int, str, str]:
        if self._prefix[0].lower().endswith((".cmd", ".bat")):
            # npm installs a .cmd shim; cmd.exe re-parses argv and mangles the JSON schema.
            raise AnalysisError(NPM_SHIM)
        # A neutral working directory keeps any project CLAUDE.md out of the context.
        with tempfile.TemporaryDirectory(
            prefix="adscout-claude-", ignore_cleanup_errors=True
        ) as cwd:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self.argv(system),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=_cli_env(),
                )
            except OSError as e:
                raise AnalysisError(
                    "Couldn't start Claude Code. Check it's installed and on your PATH."
                ) from e
            finished = False
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(user.encode("utf-8")), timeout=self._timeout_s
                )
                finished = True
            except TimeoutError:
                raise AnalysisError(_timeout_message(self._timeout_s)) from None
            finally:
                # Timeout, cancellation or any other failure: never leave claude running.
                if not finished and proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await asyncio.shield(proc.wait())
        code = proc.returncode if proc.returncode is not None else -1
        return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def _once(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        code, stdout, stderr = await self._run(system, user)
        try:
            envelope = json.loads(stdout)
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict):
            if code != 0:
                raise AnalysisError(self._failure(stderr or stdout, code))
            raise _FormatError
        if envelope.get("is_error") or code != 0:
            detail = str(envelope.get("result") or "") or stderr
            raise AnalysisError(self._failure(detail, code))

        usage = envelope.get("usage")
        payload = envelope.get("structured_output")
        try:
            if payload is None:
                result_text = envelope.get("result")
                if not isinstance(result_text, str):
                    raise _FormatError
                payload = json.loads(result_text)
            analysis = AngleAnalysis.model_validate(payload)
        except ValueError:  # JSONDecodeError and pydantic ValidationError
            raise _FormatError from None
        return analysis, usage if isinstance(usage, dict) else None

    @staticmethod
    def _failure(detail: str, code: int) -> str:
        if _looks_logged_out(detail):
            return NOT_LOGGED_IN
        tail = _tail(detail)
        return f"Claude Code failed: {tail}" if tail else f"Claude Code failed (exit code {code})."


# --- Anthropic API --------------------------------------------------------------------------


class AnthropicApiAnalyzer:
    """Structured output via ``client.beta.messages.parse`` with server-side refusal fallbacks
    where the model supports them. The SDK retries 429/5xx/connection errors itself."""

    name: BackendName = "api"

    def __init__(self, model: str, client: anthropic.AsyncAnthropic | None = None) -> None:
        self.model = model
        self._client = client if client is not None else anthropic.AsyncAnthropic()

    async def analyze(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        try:
            return await _with_one_retry(lambda: self._once(system, user))
        except anthropic.APIError as e:
            # Raised outside the except block's chain so no SDK error text (which may echo
            # request details) travels with the AnalysisError.
            message = _api_error_message(e)
        raise AnalysisError(message)

    async def _once(self, system: str, user: str) -> tuple[AngleAnalysis, dict[str, Any] | None]:
        use_fallbacks = self.model in FALLBACK_MODELS
        fallbacks: BetaFallbacksParam | anthropic.Omit = (
            "default" if use_fallbacks else anthropic.omit
        )
        betas: list[AnthropicBetaParam] | anthropic.Omit = (
            [FALLBACK_BETA] if use_fallbacks else anthropic.omit
        )
        output_config: BetaOutputConfigParam = {"effort": "medium"}
        raw = await self._client.beta.messages.with_raw_response.parse(
            model=self.model,
            max_tokens=API_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=AngleAnalysis,
            output_config=output_config,
            fallbacks=fallbacks,
            betas=betas,
        )
        # Check for a refusal before parsing: a refused reply need not match the schema.
        body = await raw.json()
        stop_reason = body.get("stop_reason") if isinstance(body, dict) else None
        if stop_reason == "refusal":
            raise AnalysisError(REFUSED)
        if stop_reason == "max_tokens":
            # A truncated answer won't parse, and retrying would be cut off the same way.
            raise AnalysisError(CUT_OFF)
        try:
            response = await raw.parse()
        except ValidationError:
            raise _FormatError from None
        if response.stop_reason == "refusal":
            raise AnalysisError(REFUSED)
        parsed = response.parsed_output
        if parsed is None:
            raise _FormatError
        return parsed, response.usage.to_dict()


def _api_error_message(e: anthropic.APIError) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "The Anthropic API rejected the API key. Check ANTHROPIC_API_KEY in .env."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This Anthropic API key isn't allowed to use that model."
    if isinstance(e, anthropic.NotFoundError):
        return "The Anthropic API doesn't recognise that model. Pick another in Settings."
    if isinstance(e, anthropic.RateLimitError):
        return "Anthropic API rate limit reached. Wait a minute and try again."
    if isinstance(e, anthropic.APITimeoutError):
        return "The Anthropic API timed out. Try again."
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach the Anthropic API. Check your internet connection."
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code == 529:
            return "The Anthropic API is overloaded. Try again in a few minutes."
        return f"Anthropic API error ({e.status_code}). Try again later."
    return "The Anthropic API request failed. Try again later."


# --- Resolution -----------------------------------------------------------------------------


class AnalysisAvailability(BaseModel):
    available: bool
    backend: BackendName | None
    reason: str | None


def _pick(settings: Settings, which: str | None) -> tuple[BackendName | None, str | None]:
    """The backend to use, or None plus the reason it's unavailable."""
    choice = which or settings.analysis_backend
    has_key = settings.anthropic_api_key is not None and bool(
        settings.anthropic_api_key.get_secret_value()
    )
    has_cli = shutil.which("claude") is not None
    if choice == "api":
        return ("api", None) if has_key else (None, NO_API_KEY)
    if choice == "cli":
        return ("cli", None) if has_cli else (None, NO_CLI)
    if has_key:
        return "api", None
    if has_cli:
        return "cli", None
    return None, UNAVAILABLE


def resolve_analyzer(
    settings: Settings, which: str | None = None, model: str | None = None
) -> Analyzer | None:
    """``auto``: the API if ANTHROPIC_API_KEY is set, else Claude Code if installed, else None."""
    backend, _ = _pick(settings, which)
    model = model or settings.analysis_model
    if backend == "api":
        assert settings.anthropic_api_key is not None
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        return AnthropicApiAnalyzer(model, client=client)
    if backend == "cli":
        return ClaudeCliAnalyzer(model, executable=shutil.which("claude") or "claude")
    return None


def analysis_status(settings: Settings) -> AnalysisAvailability:
    backend, reason = _pick(settings, None)
    return AnalysisAvailability(available=backend is not None, backend=backend, reason=reason)
