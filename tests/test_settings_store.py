import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adscout.config import Settings
from adscout.settings_store import (
    UserSettings,
    effective_settings,
    load_user_settings,
    save_user_settings,
    update_user_settings,
)


def _base(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, max_results=400)


def test_settings_store_roundtrip_defaults_on_corrupt_file(tmp_path: Path) -> None:
    assert load_user_settings(tmp_path) == UserSettings()

    saved = UserSettings(analysis_model="claude-opus-5-5", auto_analyze=True, max_results=50)
    save_user_settings(tmp_path, saved)
    assert load_user_settings(tmp_path) == saved
    assert not list(tmp_path.glob("*.tmp"))

    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert load_user_settings(tmp_path) == UserSettings()

    (tmp_path / "settings.json").write_text(json.dumps({"max_results": 0}), encoding="utf-8")
    assert load_user_settings(tmp_path) == UserSettings()


def test_scroll_delay_validation() -> None:
    UserSettings(scroll_delay_min=0.5, scroll_delay_max=30)
    UserSettings(scroll_delay_min=3, scroll_delay_max=3)
    for lo, hi in [(0.4, 5), (6, 5), (2, 31), (-1, 2)]:
        with pytest.raises(ValidationError):
            UserSettings(scroll_delay_min=lo, scroll_delay_max=hi)
    for n in (0, 1001):
        with pytest.raises(ValidationError):
            UserSettings(max_results=n)
    with pytest.raises(ValidationError):
        UserSettings(analysis_model="gpt-4")


def test_effective_settings_user_file_wins_only_for_set_fields(tmp_path: Path) -> None:
    base = _base(tmp_path)
    eff = effective_settings(base)
    assert eff.max_results == 400
    assert eff.analysis_backend == "auto"
    assert eff.analysis_model == "claude-sonnet-5-5"

    update_user_settings(tmp_path, {"analysis_backend": "cli", "scroll_delay_max": 9})
    eff = effective_settings(base)
    assert eff.analysis_backend == "cli"
    assert eff.scroll_delay_max == 9
    assert eff.max_results == 400

    update_user_settings(tmp_path, {"max_results": 20})
    eff = effective_settings(base)
    assert (eff.analysis_backend, eff.max_results) == ("cli", 20)


def test_update_rejects_invalid_and_keeps_file(tmp_path: Path) -> None:
    update_user_settings(tmp_path, {"auto_analyze": True})
    with pytest.raises(ValidationError):
        update_user_settings(tmp_path, {"scroll_delay_min": 10, "scroll_delay_max": 2})
    assert load_user_settings(tmp_path).auto_analyze is True
    assert load_user_settings(tmp_path).scroll_delay_min == 2.0


def test_single_saved_delay_is_range_checked_only(tmp_path: Path) -> None:
    # The pair is checked by the caller against .env; a lone delay must still load back.
    update_user_settings(tmp_path, {"scroll_delay_max": 1.5})
    loaded = load_user_settings(tmp_path)
    assert loaded.scroll_delay_max == 1.5
    assert loaded.model_dump(exclude_unset=True) == {"scroll_delay_max": 1.5}
    with pytest.raises(ValidationError):
        update_user_settings(tmp_path, {"scroll_delay_max": 31})


def test_effective_scroll_delays_clamped(tmp_path: Path) -> None:
    # .env says min 8; the saved file only lowers max to 5: min must not exceed max.
    base = Settings(_env_file=None, data_dir=tmp_path, scroll_delay_min=8.0, scroll_delay_max=10.0)
    save_user_settings(tmp_path, UserSettings.model_validate({"scroll_delay_max": 5.0}))
    eff = effective_settings(base)
    assert eff.scroll_delay_min <= eff.scroll_delay_max
    assert eff.scroll_delay_max == 5.0

    # Out-of-range .env values are clamped into the allowed range.
    wild = Settings(
        _env_file=None, data_dir=tmp_path / "x", scroll_delay_min=0.0, scroll_delay_max=99.0
    )
    eff = effective_settings(wild)
    assert (eff.scroll_delay_min, eff.scroll_delay_max) == (0.5, 30.0)


def test_watch_settings_defaults_and_validation(tmp_path: Path) -> None:
    eff = effective_settings(_base(tmp_path))
    assert eff.daily_check_time == "09:00"
    assert eff.desktop_notifications is False
    assert eff.max_auto_analyses_per_day == 5
    update_user_settings(tmp_path, {"daily_check_time": "7:05"})
    assert effective_settings(_base(tmp_path)).daily_check_time == "07:05"
    for bad in ["24:00", "9", "ab:cd", "09:60"]:
        with pytest.raises(ValidationError):
            UserSettings(daily_check_time=bad)
    with pytest.raises(ValidationError):
        UserSettings(max_auto_analyses_per_day=-1)
