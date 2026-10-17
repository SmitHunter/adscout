from pathlib import Path

import pytest

from adscout.config import Settings


def test_empty_env_values_count_as_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("META_ACCESS_TOKEN=\nANTHROPIC_API_KEY=\n", encoding="utf-8")

    settings = Settings(_env_file=env)

    assert settings.meta_access_token is None
    assert settings.anthropic_api_key is None
