"""Optional official Meta Ad Library API source (EU/UK ads only)."""

import logging
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from adscout.models import Ad, SearchQuery
from adscout.sources.base import (
    CancelToken,
    ProgressCallback,
    SearchResult,
    SourceBlocked,
    SourceError,
    emit_progress,
)

logger = logging.getLogger(__name__)

API_URL = "https://graph.facebook.com/v23.0/ads_archive"
GRAPH_PREFIX = "https://graph.facebook.com/"
PAGE_SIZE = 100
TIMEOUT = 30.0

EU27 = frozenset(
    [
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
    ]
)
SUPPORTED = EU27 | {"GB", "EU"}

FIELDS = ",".join(
    (
        "id",
        "page_id",
        "page_name",
        "ad_delivery_start_time",
        "ad_delivery_stop_time",
        "ad_creative_bodies",
        "ad_creative_link_titles",
        "ad_creative_link_captions",
        "ad_snapshot_url",
        "publisher_platforms",
        "eu_total_reach",
        "languages",
    )
)

TOKEN_REJECTED = SourceBlocked(
    "Meta API token rejected.",
    "Create a new token at developers.facebook.com/tools/explorer "
    "and set META_ACCESS_TOKEN in .env.",
)
REFUSED = "Meta API refused the request."
RATE_LIMIT_CODES = frozenset({4, 17, 613})
TOKEN_SUBCODES = range(458, 468)  # 458-467: expired / invalidated / logged-out sessions


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _parse_date(value: str) -> datetime:
    """Take the 'YYYY-MM-DD' part (any time suffix is dropped) as aware UTC midnight."""
    return datetime.fromisoformat(value.strip()[:10]).replace(tzinfo=UTC)


def _require_id(item: dict[str, Any]) -> str:
    value = item["id"]
    if not value:
        raise ValueError("empty id")
    return str(value)


def _to_ad(item: dict[str, Any]) -> Ad:
    return Ad(
        source="meta_api",
        ad_archive_id=_require_id(item),
        page_id=str(item.get("page_id") or ""),
        page_name=str(item.get("page_name") or "").strip(),
        start_date=_parse_date(item["ad_delivery_start_time"]),
        end_date=(
            _parse_date(item["ad_delivery_stop_time"])
            if item.get("ad_delivery_stop_time")
            else None
        ),
        is_active=not item.get("ad_delivery_stop_time"),
        body=_first(item.get("ad_creative_bodies")) or "",
        title=_first(item.get("ad_creative_link_titles")),
        link_url=None,  # the API does not expose the landing URL
        link_caption=_first(item.get("ad_creative_link_captions")),
        media=[],  # the API has no direct media URLs
        platforms=[str(p) for p in item.get("publisher_platforms") or []],
        raw=_strip_token(item),
    )


def _strip_token(item: dict[str, Any]) -> dict[str, Any]:
    """Copy of ``item`` with any ``access_token`` removed from ``ad_snapshot_url``."""
    raw = dict(item)
    snapshot_url = raw.get("ad_snapshot_url")
    if isinstance(snapshot_url, str):
        try:
            urlsplit(snapshot_url)  # httpx is lenient; this rejects e.g. bad IPv6 hosts
            cleaned = httpx.URL(snapshot_url).copy_remove_param("access_token")
        except httpx.InvalidURL as exc:
            raise ValueError("malformed ad_snapshot_url") from exc
        raw["ad_snapshot_url"] = str(cleaned)
    return raw


def _graph_error(response: httpx.Response, token: str) -> SourceBlocked | None:
    """Classify a Graph API error payload; None when it is not an auth/limit error.

    Only code 190 (or subcodes 458-467) means the token was rejected. Other
    OAuthException / rate-limit errors are reported with Graph's code and message.
    The token value is scrubbed from the message.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    subcode = error.get("error_subcode")
    if code == 190 or subcode in TOKEN_SUBCODES:
        return TOKEN_REJECTED
    if error.get("type") != "OAuthException" and code not in RATE_LIMIT_CODES:
        return None
    message = str(error.get("message") or "no message").strip().rstrip(".")
    if token:
        message = message.replace(token, "<redacted>")
    if code in RATE_LIMIT_CODES:
        fix = f"Meta API rate limit hit (code {code}): {message}. Wait an hour and retry."
    else:
        fix = (
            f"Meta API error (code {code}): {message}. "
            "Check the token's app has Ad Library API access."
        )
    return SourceBlocked(REFUSED, fix)


class MetaApiSource:
    name = "meta_api"

    def __init__(
        self,
        token: SecretStr,
        client: httpx.AsyncClient | None = None,
        max_results: int = 300,
    ) -> None:
        self._token = token
        self._client = client
        self._max_results = max_results

    def supports(self, q: SearchQuery) -> bool:
        # Keyword searches only: advertiser page views go through the web source.
        return q.kind == "keyword" and q.country in SUPPORTED

    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult:
        if self._client is not None:
            return await self._run(self._client, q, progress, cancel)
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await self._run(client, q, progress, cancel)

    async def _run(
        self,
        client: httpx.AsyncClient,
        q: SearchQuery,
        progress: ProgressCallback | None,
        cancel: CancelToken | None,
    ) -> SearchResult:
        cap = min(q.limit, self._max_results)
        countries = sorted(EU27) if q.country == "EU" else [q.country]
        params: dict[str, str] | None = {
            "search_terms": q.keyword,
            "ad_reached_countries": "[" + ",".join(f'"{c}"' for c in countries) + "]",
            "ad_active_status": "ACTIVE" if q.active_status == "active" else "ALL",
            "ad_type": "ALL",
            "media_type": q.media_type.upper(),
            "fields": FIELDS,
            "limit": str(PAGE_SIZE),
        }
        url: str | None = API_URL
        ads: dict[str, Ad] = {}

        def done(status: Literal["ok", "blocked", "cancelled", "capped"]) -> SearchResult:
            return SearchResult(ads=list(ads.values())[:cap], status=status)

        while url is not None:
            payload = await self._fetch(client, url, params)
            if isinstance(payload, SourceBlocked):
                return SearchResult(status="blocked", message=str(payload))
            for item in payload.get("data", []):
                try:
                    ad = _to_ad(item)
                except (KeyError, TypeError, ValueError, AttributeError):
                    logger.warning("Skipping malformed Meta API ad item")
                    continue
                ads.setdefault(ad.ad_archive_id, ad)
            next_url = (payload.get("paging") or {}).get("next")
            url, params = None, None
            if isinstance(next_url, str):
                if next_url.startswith(GRAPH_PREFIX):
                    # Meta embeds the token in next URLs; the Bearer header is used instead.
                    url = str(httpx.URL(next_url).copy_remove_param("access_token"))
                else:
                    logger.warning("Ignoring paging.next outside graph.facebook.com")
            await emit_progress(
                progress, list(ads.values())[:cap], "scrolling", f"Fetched {len(ads)} ads"
            )
            if len(ads) >= cap and (url is not None or len(ads) > cap):
                return done("capped")
            if url is not None and cancel is not None and cancel.cancelled:
                return done("cancelled")
        if cancel is not None and cancel.cancelled:
            return done("cancelled")
        return done("ok")

    async def _fetch(
        self, client: httpx.AsyncClient, url: str, params: dict[str, str] | None
    ) -> dict[str, Any] | SourceBlocked:
        """Fetch one page, or a SourceBlocked for auth/limit errors. Never leaks the token."""
        try:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
            )
        except httpx.HTTPError as exc:
            raise SourceError(
                f"Meta Ad Library API request failed ({type(exc).__name__})."
            ) from None
        blocked = _graph_error(response, self._token.get_secret_value())
        if blocked is not None:
            return blocked
        if response.is_error:
            raise SourceError(f"Meta Ad Library API returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise SourceError("Meta Ad Library API returned invalid JSON.") from None
        if not isinstance(payload, dict):
            raise SourceError("Meta Ad Library API returned an unexpected response.")
        return payload
