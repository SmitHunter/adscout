"""Detect Meta login walls, security checks and temporary blocks."""

from urllib.parse import urlparse

from adscout.sources import SourceBlocked

_LOGIN_FIX = "Meta asked for login. Run: uv run adscout browser — sign in once, then retry."
_CHECK_FIX = (
    "Meta showed a security check. Open the browser with: uv run adscout browser, "
    "complete it yourself, wait 30+ minutes, then retry."
)
_BLOCK_FIX = "Meta rate-limited this browser. Wait a few hours before searching again."

_LOGIN = ("Login required.", _LOGIN_FIX)
_CHECK = ("Security check.", _CHECK_FIX)
_BLOCK = ("Temporarily blocked.", _BLOCK_FIX)

# Strong phrases are specific enough to scan the whole page (a login modal can
# be injected after ad cards). Weak phrases could appear in ad copy, so they
# only scan the page chrome before the first ad card.
_STRONG_RULES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("you must log in", _LOGIN),
    ("log in to continue", _LOGIN),
    ("confirm you're human", _CHECK),
    ("you're temporarily blocked", _BLOCK),
)
_WEAK_RULES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("security check", _CHECK),
    ("rate limit", _BLOCK),
)

_AD_MARKER = "Library ID:"


def detect_block(url: str, inner_text: str) -> SourceBlocked | None:
    """Return a SourceBlocked if the page is a login wall, check or block.

    URL rules always apply. Strong text rules scan the whole page; weak ones
    only scan the chrome before the first ad card so ad copy cannot trigger a
    false positive.
    """
    path = urlparse(url).path
    if path.startswith("/login"):
        return SourceBlocked(*_LOGIN)
    if "/checkpoint/" in path:
        return SourceBlocked(*_CHECK)

    def norm(text: str) -> str:
        return text.lower().replace("\u2019", "'")

    full = norm(inner_text)
    chrome = norm(inner_text.split(_AD_MARKER, 1)[0])
    for rules, haystack in ((_STRONG_RULES, full), (_WEAK_RULES, chrome)):
        for phrase, (reason, how_to_fix) in rules:
            if phrase in haystack:
                return SourceBlocked(reason, how_to_fix)
    return None
