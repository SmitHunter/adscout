"""Fallback parser: turn ``document.body.innerText`` of an Ad Library page into ``Ad`` objects."""

import logging
import re
from datetime import UTC, datetime

from adscout.models import Ad

logger = logging.getLogger(__name__)

_SEPARATOR = "Library ID:"
_DASH = f"[-{chr(0x2013)}]"
_DATE = r"(\d{1,2} \w{3} \d{4}|\w{3} \d{1,2}, \d{4})"
_ID_RE = re.compile(r"\s*(\d+)")
_START_RE = re.compile(rf"Started running on {_DATE}")
_RANGE_RE = re.compile(rf"{_DATE}\s*{_DASH}\s*{_DATE}")
_COLLATION_RE = re.compile(r"(\d+) ads use this creative")
_TIMER_RE = re.compile(r"\d+:\d{2} / \d+:\d{2}")
_CTA_LINES = frozenset(
    {
        "shop now",
        "learn more",
        "book now",
        "send message",
        "sign up",
        "order now",
        "get offer",
        "download",
        "contact us",
        "apply now",
        "watch more",
        "subscribe",
    }
)
_STATUSES = {"Active": True, "Inactive": False}
_DATE_FORMATS = ("%d %b %Y", "%b %d, %Y")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\u200b", "").splitlines()]


def _last_line(text: str) -> str:
    return next((line for line in reversed(_lines(text)) if line), "")


def _parse_date(value: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _split(text: str) -> list[tuple[str, str]]:
    """Return ``(status_line, chunk)`` pairs; the status line trails the previous segment."""
    segments = text.split(_SEPARATOR)
    pairs: list[tuple[str, str]] = []
    for i in range(1, len(segments)):
        status = _last_line(segments[i - 1])
        chunk = segments[i]
        if i < len(segments) - 1 and _last_line(chunk) in _STATUSES:
            chunk = chunk.rstrip()
            chunk = chunk[: len(chunk) - len(_last_line(chunk))].rstrip()
        pairs.append((status, chunk))
    return pairs


def _advertiser_and_body(chunk: str) -> tuple[str, str]:
    lines = _lines(chunk)
    if "Sponsored" not in lines:
        return "", ""
    idx = lines.index("Sponsored")
    advertiser = next((line for line in reversed(lines[:idx]) if line), "")
    body: list[str] = []
    for line in lines[idx + 1 :]:
        if _TIMER_RE.fullmatch(line):
            break
        body.append(line)
    while body and not body[-1]:
        body.pop()
    if body and body[-1].lower() in _CTA_LINES:
        body.pop()
    return advertiser, "\n".join(body).strip()


def parse_inner_text(text: str, source: str = "meta_web_text") -> list[Ad]:
    """Parse page innerText; chunks without an id or start date are skipped with a warning."""
    ads: list[Ad] = []
    for status, chunk in _split(text):
        id_match = _ID_RE.match(chunk)
        start_match = _START_RE.search(chunk)
        start = _parse_date(start_match.group(1)) if start_match else None
        if id_match is None or start is None:
            logger.warning("Skipping text chunk %r: missing id or start date", chunk[:40])
            continue
        end: datetime | None = None
        range_match = _RANGE_RE.search(chunk)
        if range_match:
            end = _parse_date(range_match.group(2))
        count_match = _COLLATION_RE.search(chunk)
        advertiser, body = _advertiser_and_body(chunk)
        ads.append(
            Ad(
                source=source,
                ad_archive_id=id_match.group(1),
                page_id="",
                page_name=advertiser,
                start_date=start,
                end_date=end,
                is_active=_STATUSES.get(status, False),
                body=body,
                title=None,
                link_url=None,
                link_caption=None,
                cta_text=None,
                display_format=None,
                collation_count=int(count_match.group(1)) if count_match else 1,
                raw={"text": chunk},
            )
        )
    return ads
