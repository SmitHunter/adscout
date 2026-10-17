"""Parse Meta Ad Library GraphQL / embedded JSON payloads into ``Ad`` objects."""

import json
import logging
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from adscout.models import Ad, MediaItem

logger = logging.getLogger(__name__)

_XSSI_PREFIX = "for (;;);"
_SCRIPT_RE = re.compile(
    r"<script\b[^>]*\btype=[\"']application/json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


def iter_json_documents(text: str) -> Iterator[Any]:
    """Yield each JSON document in ``text`` (XSSI prefix stripped, concatenated/NDJSON ok)."""
    decoder = json.JSONDecoder()
    s = text.lstrip()
    if s.startswith(_XSSI_PREFIX):
        s = s[len(_XSSI_PREFIX) :]
    pos = 0
    n = len(s)
    while pos < n:
        while pos < n and s[pos].isspace():
            pos += 1
        if pos >= n:
            return
        if s.startswith(_XSSI_PREFIX, pos):
            pos += len(_XSSI_PREFIX)
            continue
        try:
            doc, pos = decoder.raw_decode(s, pos)
        except ValueError:
            logger.warning("Could not decode JSON at offset %d; stopping", pos)
            return
        yield doc


def extract_embedded_json(html: str) -> list[Any]:
    """Return parsed bodies of all ``<script type="application/json">`` tags with ads."""
    docs: list[Any] = []
    for match in _SCRIPT_RE.finditer(html):
        body = match.group(1)
        if "ad_archive_id" not in body:
            continue
        try:
            docs.append(json.loads(body))
        except ValueError:
            logger.warning("Skipping undecodable embedded JSON script (%d bytes)", len(body))
    return docs


def find_ad_nodes(doc: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict holding both ``ad_archive_id`` and ``snapshot`` (any depth)."""
    stack: list[Any] = [doc]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "ad_archive_id" in cur and "snapshot" in cur:
                yield cur
                continue
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _body_text(body: Any) -> str | None:
    """Body text, or None when missing or blank (so the first-card fallback runs)."""
    text = body.get("text") if isinstance(body, dict) else body
    return text if isinstance(text, str) and text.strip() else None


def _name(value: Any) -> str | None:
    """Stripped advertiser name, or None when missing or blank."""
    if value is None:
        return None
    return str(value).strip() or None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _image_url(d: dict[str, Any]) -> str | None:
    return _str(d.get("original_image_url")) or _str(d.get("resized_image_url"))


def _video(d: dict[str, Any]) -> MediaItem | None:
    url = _str(d.get("video_hd_url")) or _str(d.get("video_sd_url"))
    if url is None:
        return None
    return MediaItem(type="video", url=url, preview_url=_str(d.get("video_preview_image_url")))


def _media(snapshot: dict[str, Any]) -> list[MediaItem]:
    """Top-level images then videos; if none, one item per card (video wins over image)."""
    items = [
        MediaItem(type="image", url=u)
        for i in _dicts(snapshot.get("images"))
        if (u := _image_url(i))
    ]
    items += [v for d in _dicts(snapshot.get("videos")) if (v := _video(d))]
    if items:
        return items
    for card in _dicts(snapshot.get("cards")):
        if (vid := _video(card)) is not None:
            items.append(vid)
        elif (u := _image_url(card)) is not None:
            items.append(MediaItem(type="image", url=u))
    return items


def _ts(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def node_to_ad(node: dict[str, Any], source: str = "meta_web") -> Ad | None:
    """Convert one ad node to an ``Ad``; return None (and log) if it is unusable."""
    ad_id = node.get("ad_archive_id")
    try:
        start = _ts(node.get("start_date"))
        if not ad_id or start is None:
            logger.warning("Skipping ad node %r: missing id or start_date", ad_id)
            return None
        snapshot = node.get("snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        cards = [c for c in snapshot.get("cards") or [] if isinstance(c, dict)]
        body = _body_text(snapshot.get("body"))
        if body is None and cards:
            body = _body_text(cards[0].get("body"))
        page_id = node.get("page_id") or snapshot.get("page_id")
        page_name = _name(node.get("page_name")) or _name(snapshot.get("page_name"))
        count = node.get("collation_count")
        platforms = node.get("publisher_platform")
        return Ad(
            source=source,
            ad_archive_id=str(ad_id),
            page_id=str(page_id) if page_id is not None else "",
            page_name=page_name or "",
            start_date=start,
            end_date=_ts(node.get("end_date")),
            is_active=bool(node.get("is_active")),
            body=body or "",
            title=_str(snapshot.get("title")),
            link_url=_str(snapshot.get("link_url")),
            link_caption=_str(snapshot.get("caption")),
            cta_text=_str(snapshot.get("cta_text")),
            display_format=_str(snapshot.get("display_format")),
            media=_media(snapshot),
            collation_id=_str(node.get("collation_id")),
            collation_count=count if isinstance(count, int) and count > 0 else 1,
            platforms=[p for p in platforms if isinstance(p, str)]
            if isinstance(platforms, list)
            else [],
            raw=node,
        )
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        logger.warning("Skipping ad node %r: %s", ad_id, exc)
        return None


def parse_payloads(texts: Iterable[str]) -> list[Ad]:
    """Parse HTML pages and/or GraphQL bodies; dedupe by ``ad_archive_id`` (first wins)."""
    seen: set[str] = set()
    ads: list[Ad] = []
    for text in texts:
        if text.lstrip().startswith("<"):
            docs: Iterable[Any] = extract_embedded_json(text)
        else:
            docs = iter_json_documents(text)
        for doc in docs:
            for node in find_ad_nodes(doc):
                ad = node_to_ad(node)
                if ad is None or ad.ad_archive_id in seen:
                    continue
                seen.add(ad.ad_archive_id)
                ads.append(ad)
    return ads
