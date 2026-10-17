"""Pick the Source for a query: shared by the CLI and the server."""

from collections.abc import Callable

from adscout.config import Settings
from adscout.models import SearchQuery
from adscout.settings_store import effective_settings
from adscout.sources.base import Source
from adscout.sources.meta_api import MetaApiSource
from adscout.sources.meta_web.source import MetaWebSource


def get_source(q: SearchQuery, settings: Settings, choice: str = "auto") -> Source:
    """Pick the Source: the official API for EU/UK when a token is set, else the web scraper."""
    token = settings.meta_access_token
    if token is not None and choice != "web":
        api = MetaApiSource(token, max_results=settings.max_results)
        if choice == "api" or api.supports(q):
            return api
    return MetaWebSource(settings)


def unsupported_message(q: SearchQuery, source: Source, settings: Settings) -> str:
    """Why ``source`` cannot run ``q``, in words the user can act on."""
    if q.kind == "advertiser" and source.name != "meta_web":
        return "Advertiser searches run on the web scraper only. Use --source web."
    if q.country == "EU" and settings.meta_access_token is None:
        return (
            "EU searches are only available through the official Meta Ad Library API. "
            "Set META_ACCESS_TOKEN in .env (see .env.example)."
        )
    return f"Country {q.country} is not supported by the {source.name} source."


def source_factory(settings: Settings) -> Callable[[SearchQuery], Source]:
    """A factory for SearchManager; raises ValueError when no source supports the query.

    The user's saved settings (max results, scroll delays) are re-read for every search,
    so a change on the Settings screen applies to the next search without a restart.
    """

    def factory(q: SearchQuery) -> Source:
        current = effective_settings(settings)
        source = get_source(q, current)
        if not source.supports(q):
            raise ValueError(unsupported_message(q, source, current))
        return source

    return factory
