"""Shared test setup: every test runs as if the shell and ``.env`` set nothing."""

import os
from collections.abc import Iterator

import pytest

from adscout.config import Settings, get_settings
from adscout.services.watchlist import clear_summary_cache

# Settings read these (case-insensitively) from the environment and ``.env``.
_SETTINGS_ENV = frozenset(name.lower() for name in Settings.model_fields)


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the developer's own AdScout configuration from tests.

    An exported ``MAX_RESULTS`` or ``ANTHROPIC_API_KEY``, or a ``.env`` in the working
    directory, would otherwise change defaults that tests assert on.
    """
    for name in list(os.environ):
        if name.lower() in _SETTINGS_ENV:
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fresh_summary_cache() -> Iterator[None]:
    """Each test starts with an empty watchlist summary cache (it is process-wide)."""
    clear_summary_cache()
    yield
    clear_summary_cache()
