import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AnalysisBackend = Literal["auto", "cli", "api"]
AnalysisModel = Literal["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
DEFAULT_ANALYSIS_MODEL: AnalysisModel = "claude-sonnet-5-5"
DEFAULT_DAILY_CHECK_TIME = "09:00"
DEFAULT_MAX_AUTO_ANALYSES_PER_DAY = 5

_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


def parse_hhmm(value: str) -> tuple[int, int]:
    """``"H:MM"``/``"HH:MM"`` (24 h) as (hour, minute); raises ``ValueError`` otherwise."""
    m = _HHMM.fullmatch(value.strip())
    if m is None or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError("Use a 24-hour time like 09:00.")
    return int(m[1]), int(m[2])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    data_dir: Path = Path("data")
    max_results: int = 300
    scroll_delay_min: float = 2.0
    scroll_delay_max: float = 5.0
    headless: bool = False
    meta_access_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    timezone: str = "Australia/Melbourne"
    analysis_backend: AnalysisBackend = "auto"
    analysis_model: AnalysisModel = DEFAULT_ANALYSIS_MODEL
    auto_analyze: bool = False
    daily_check_time: str = DEFAULT_DAILY_CHECK_TIME  # "HH:MM" in ``timezone``
    desktop_notifications: bool = False
    max_auto_analyses_per_day: int = DEFAULT_MAX_AUTO_ANALYSES_PER_DAY

    @property
    def db_path(self) -> Path:
        return self.data_dir / "adscout.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"


@lru_cache
def get_settings() -> Settings:
    return Settings()
