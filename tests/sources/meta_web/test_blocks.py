from pathlib import Path

import pytest

from adscout.models import SearchQuery
from adscout.sources import SourceBlocked
from adscout.sources.meta_web import blocks
from adscout.sources.meta_web.blocks import detect_block
from adscout.sources.meta_web.urls import build_library_url

FIXTURES = Path(__file__).parents[2] / "fixtures" / "meta_web"
LIBRARY = "https://www.facebook.com/ads/library/?q=x"


def _read(path: str) -> str:
    return (FIXTURES / path).read_text(encoding="utf-8")


def test_login_url_detected() -> None:
    exc = detect_block("https://www.facebook.com/login/?next=abc", "")
    assert isinstance(exc, SourceBlocked)
    assert "uv run adscout browser" in str(exc)
    assert "sign in once" in str(exc)


def test_checkpoint_url_detected() -> None:
    exc = detect_block("https://www.facebook.com/checkpoint/1501092823525282/", "")
    assert isinstance(exc, SourceBlocked)
    assert "security check" in str(exc)


def test_login_wall_text_detected() -> None:
    exc = detect_block(LIBRARY, _read("blocks/login_wall.txt"))
    assert isinstance(exc, SourceBlocked)
    assert "sign in once" in str(exc)


def test_checkpoint_text_detected() -> None:
    exc = detect_block(LIBRARY, _read("blocks/checkpoint.txt"))
    assert isinstance(exc, SourceBlocked)
    assert "complete it yourself" in str(exc)


def test_temp_block_text_detected() -> None:
    exc = detect_block(LIBRARY, _read("blocks/temp_block.txt"))
    assert isinstance(exc, SourceBlocked)
    assert "Wait a few hours" in str(exc)


@pytest.mark.parametrize(
    "phrase",
    [
        "You must log in",
        "LOG IN TO CONTINUE",
        "Security Check",
        "Confirm you're human",
        "You're temporarily blocked",
        "Rate limit",
    ],
)
def test_each_phrase_detected_case_insensitively(phrase: str) -> None:
    assert detect_block(LIBRARY, f"Meta\n{phrase}\n") is not None


LOGIN_FIX = "Meta asked for login. Run: uv run adscout browser — sign in once, then retry."
CHECK_FIX = (
    "Meta showed a security check. Open the browser with: uv run adscout browser, "
    "complete it yourself, wait 30+ minutes, then retry."
)
BLOCK_FIX = "Meta rate-limited this browser. Wait a few hours before searching again."


@pytest.mark.parametrize(
    ("url", "text", "reason", "how_to_fix"),
    [
        ("https://www.facebook.com/login/?next=x", "", "Login required.", LOGIN_FIX),
        ("https://www.facebook.com/checkpoint/1/", "", "Security check.", CHECK_FIX),
        (LIBRARY, "Meta\nYou must log in\n", "Login required.", LOGIN_FIX),
        (LIBRARY, "Meta\nLog in to continue\n", "Login required.", LOGIN_FIX),
        (LIBRARY, "Meta\nConfirm you're human\n", "Security check.", CHECK_FIX),
        (LIBRARY, "Meta\nYou're temporarily blocked\n", "Temporarily blocked.", BLOCK_FIX),
        (LIBRARY, "Meta\nSecurity check\n", "Security check.", CHECK_FIX),
        (LIBRARY, "Meta\nRate limit exceeded\n", "Temporarily blocked.", BLOCK_FIX),
    ],
)
def test_each_rule_gives_its_exact_reason_and_fix(
    url: str, text: str, reason: str, how_to_fix: str
) -> None:
    exc = detect_block(url, text)
    assert exc is not None
    assert (exc.reason, exc.how_to_fix) == (reason, how_to_fix)
    assert str(exc) == f"{reason} {how_to_fix}"


def test_normal_results_page_not_blocked() -> None:
    q = SearchQuery(keyword="posture corrector", country="US")
    text = _read("posture-corrector-US/inner_text.txt")
    assert detect_block(build_library_url(q), text) is None


def test_empty_results_page_not_blocked() -> None:
    q = SearchQuery(keyword="zzqxwv nothing", country="US")
    text = _read("zzqxwv-nothing-US/inner_text.txt")
    assert detect_block(build_library_url(q), text) is None


def test_ad_body_phrase_after_library_id_not_blocked() -> None:
    text = (
        "Meta Ad Library\nAll ads\n"
        "Library ID: 123\nActive\nRun a security check on your PC. "
        "Beat the rate limit.\n"
    )
    assert detect_block(LIBRARY, text) is None


def test_phrase_before_library_id_still_detected() -> None:
    text = "Security check\nLibrary ID: 123\nBuy now\n"
    assert detect_block(LIBRARY, text) is not None


def test_rd_verify_url_on_library_path_not_blocked() -> None:
    url = "https://www.facebook.com/ads/library/?q=x&__rd_verify_abc123=1"
    assert detect_block(url, "Meta Ad Library\nAll ads\n") is None


def test_curly_apostrophe_detected() -> None:
    assert detect_block(LIBRARY, "Confirm you\u2019re human") is not None


def test_login_modal_after_ad_cards_detected() -> None:
    text = (
        "Meta Ad Library\nAll ads\nLibrary ID: 123\nActive\nBuy now\n"
        "Library ID: 456\nActive\nLog in to continue\nEmail\nPassword\n"
    )
    exc = detect_block(LIBRARY, text)
    assert exc is not None
    assert exc.how_to_fix == (
        "Meta asked for login. Run: uv run adscout browser — sign in once, then retry."
    )


def test_rate_limit_in_ad_body_not_blocked() -> None:
    text = "Meta Ad Library\nLibrary ID: 123\nActive\nBeat the rate limit today.\n"
    assert detect_block(LIBRARY, text) is None


def test_block_copy_is_pinned() -> None:
    # The GUI maps these exact strings in web/src/features/search/form.ts
    # (splitBlocked / forTheApp). Changing them requires updating that file.
    assert blocks._LOGIN == (
        "Login required.",
        "Meta asked for login. Run: uv run adscout browser — sign in once, then retry.",
    )
    assert blocks._CHECK == (
        "Security check.",
        "Meta showed a security check. Open the browser with: uv run adscout browser, "
        "complete it yourself, wait 30+ minutes, then retry.",
    )
    assert blocks._BLOCK == (
        "Temporarily blocked.",
        "Meta rate-limited this browser. Wait a few hours before searching again.",
    )
