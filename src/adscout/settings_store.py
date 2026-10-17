"""User-editable settings, stored as ``<data_dir>/settings.json``.

Only fields the user has explicitly set are written, so a value from ``.env`` still applies
until the user overrides it in the Settings screen. A missing or corrupt file means
"no overrides", never a crash.
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adscout.config import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_DAILY_CHECK_TIME,
    DEFAULT_MAX_AUTO_ANALYSES_PER_DAY,
    AnalysisBackend,
    AnalysisModel,
    Settings,
    get_settings,
    parse_hhmm,
)

SETTINGS_FILE = "settings.json"
SCROLL_DELAY_MIN = 0.5
SCROLL_DELAY_MAX = 30.0


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    analysis_backend: AnalysisBackend = "auto"
    analysis_model: AnalysisModel = DEFAULT_ANALYSIS_MODEL
    auto_analyze: bool = False
    scroll_delay_min: float = 2.0
    scroll_delay_max: float = 5.0
    max_results: int = Field(default=300, ge=1, le=1000)
    daily_check_time: str = DEFAULT_DAILY_CHECK_TIME
    desktop_notifications: bool = False
    max_auto_analyses_per_day: int = Field(default=DEFAULT_MAX_AUTO_ANALYSES_PER_DAY, ge=0, le=100)

    @field_validator("daily_check_time")
    @classmethod
    def _daily_check_time(cls, v: str) -> str:
        hour, minute = parse_hhmm(v)
        return f"{hour:02d}:{minute:02d}"

    @model_validator(mode="after")
    def _scroll_delays(self) -> Self:
        lo, hi = self.scroll_delay_min, self.scroll_delay_max
        in_range = all(SCROLL_DELAY_MIN <= v <= SCROLL_DELAY_MAX for v in (lo, hi))
        # With only one delay saved, the other comes from .env: the caller checks that pair.
        both = {"scroll_delay_min", "scroll_delay_max"} <= self.model_fields_set
        neither = not ({"scroll_delay_min", "scroll_delay_max"} & self.model_fields_set)
        if not in_range or ((both or neither) and lo > hi):
            raise ValueError(
                f"Scroll delays must satisfy {SCROLL_DELAY_MIN} ≤ min ≤ max ≤ {SCROLL_DELAY_MAX:g}."
            )
        return self


def settings_path(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE


def load_user_settings(data_dir: Path) -> UserSettings:
    """The saved overrides, or defaults (with nothing marked as set) if unreadable."""
    try:
        raw = json.loads(settings_path(data_dir).read_text(encoding="utf-8"))
        return UserSettings.model_validate(raw)
    except (OSError, ValueError):
        # ValueError covers JSONDecodeError and pydantic's ValidationError.
        return UserSettings()


def save_user_settings(data_dir: Path, settings: UserSettings) -> None:
    """Write the explicitly-set fields atomically (temp file + replace)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = settings_path(data_dir)
    tmp = path.with_name(path.name + ".tmp")
    payload = settings.model_dump(mode="json", exclude_unset=True)
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def update_user_settings(data_dir: Path, patch: Mapping[str, Any]) -> UserSettings:
    """Merge ``patch`` into the saved overrides, validate, save and return them.

    Raises pydantic's ``ValidationError`` on invalid values and leaves the file untouched.
    """
    current = load_user_settings(data_dir).model_dump(exclude_unset=True)
    updated = UserSettings.model_validate({**current, **patch})
    save_user_settings(data_dir, updated)
    return updated


def effective_settings(base: Settings | None = None) -> Settings:
    """The env/.env settings with the user's saved overrides applied on top.

    The scroll delays are clamped into range with min ≤ max: ``.env`` and the saved file
    are validated separately, so together they can disagree.
    """
    base = base if base is not None else get_settings()
    overrides = load_user_settings(base.data_dir).model_dump(exclude_unset=True)
    merged = base.model_copy(update=overrides)
    lo = min(max(merged.scroll_delay_min, SCROLL_DELAY_MIN), SCROLL_DELAY_MAX)
    hi = min(max(merged.scroll_delay_max, SCROLL_DELAY_MIN), SCROLL_DELAY_MAX)
    if lo > hi:
        # Keep the value the user saved; move the other one to meet it.
        if "scroll_delay_max" in overrides and "scroll_delay_min" not in overrides:
            lo = hi
        else:
            hi = lo
    return merged.model_copy(update={"scroll_delay_min": lo, "scroll_delay_max": hi})
