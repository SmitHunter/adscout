from urllib.parse import urlencode

from adscout.models import SearchQuery

LIBRARY_URL = "https://www.facebook.com/ads/library/"


def build_library_url(q: SearchQuery) -> str:
    """The Ad Library URL: a keyword search, or one advertiser's page view."""
    params: dict[str, str]
    if q.kind == "advertiser":
        assert q.page_id is not None  # SearchQuery requires it for this kind
        params = {
            "active_status": q.active_status,
            "ad_type": "all",
            "country": q.country,
            "view_all_page_id": q.page_id,
            "search_type": "page",
            "media_type": q.media_type,
        }
    else:
        params = {
            "active_status": q.active_status,
            "ad_type": "all",
            "country": q.country,
            "q": q.keyword,
            "search_type": "keyword_unordered",
            "media_type": q.media_type,
        }
    return f"{LIBRARY_URL}?{urlencode(params)}"
