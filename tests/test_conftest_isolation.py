"""The autouse fixture in conftest.py hides ambient AdScout settings."""

import os
from pathlib import Path

import pytest

from adscout.config import Settings


def test_ambient_settings_env_is_hidden() -> None:
    names = {n.lower() for n in os.environ}
    assert not names & {"max_results", "data_dir", "anthropic_api_key", "headless"}
    assert Settings().max_results == 300


def test_dotenv_in_working_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MAX_RESULTS=7\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert Settings().max_results == 300
