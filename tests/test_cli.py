from pathlib import Path

from typer.testing import CliRunner

from adscout.cli import app
from adscout.config import Settings


def test_version_prints() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.max_results == 300
    assert settings.db_path == Path("data/adscout.db")
