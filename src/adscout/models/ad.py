from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from adscout.models.countries import COUNTRY_ALIASES, SUPPORTED_COUNTRIES

MediaType = Literal["image", "video"]


class MediaItem(BaseModel):
    type: MediaType
    url: str
    preview_url: str | None = None
    local_path: str | None = None
    poster_path: str | None = None
    phash: str | None = None


class Ad(BaseModel):
    source: str
    ad_archive_id: str
    page_id: str
    page_name: str
    start_date: AwareDatetime
    end_date: AwareDatetime | None = None
    is_active: bool
    body: str = ""
    title: str | None = None
    link_url: str | None = None
    link_caption: str | None = None
    cta_text: str | None = None
    display_format: str | None = None
    media: list[MediaItem] = Field(default_factory=list)
    collation_id: str | None = None
    collation_count: int = Field(1, ge=1)
    platforms: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def advertiser_key(self) -> str | None:
        """Page id, else the stripped page name; None when the ad has neither."""
        return self.page_id or self.page_name.strip() or None

    def days_running(self, now: datetime) -> int:
        """Whole days the ad has run; ended ads count start to end."""
        end = self.end_date if not self.is_active and self.end_date else now
        return (end - self.start_date).days


SearchKind = Literal["keyword", "advertiser"]

# Advertiser (page-view) searches may cover every country at once.
ALL_COUNTRIES = "ALL"


class SearchQuery(BaseModel):
    """A keyword search, or (``kind="advertiser"``) one advertiser's page in the Ad Library.

    For an advertiser, ``keyword`` holds the display name, ``page_id`` the numeric page id,
    and ``country`` defaults to ``"ALL"``.
    """

    kind: SearchKind = "keyword"
    keyword: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    page_id: str | None = None
    country: str = "US"
    active_status: Literal["active", "all"] = "active"
    media_type: Literal["all", "image", "video", "meme"] = "all"
    limit: int = Field(default=300, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def _advertiser_country_default(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("kind") == "advertiser" and "country" not in data:
            return {**data, "country": ALL_COUNTRIES}
        return data

    @field_validator("country")
    @classmethod
    def _country(cls, v: str, info: ValidationInfo) -> str:
        v = v.strip().upper()
        if v == ALL_COUNTRIES and info.data.get("kind") == "advertiser":
            return v
        if len(v) != 2 or not (v.isascii() and v.isalpha()):
            raise ValueError("country must be two letters (e.g. US, AU, EU)")
        v = COUNTRY_ALIASES.get(v, v)
        if v not in SUPPORTED_COUNTRIES:
            raise ValueError(f"unknown country code {v!r} (use ISO codes like US, GB, AU, or EU)")
        return v

    @field_validator("page_id")
    @classmethod
    def _page_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (v.isascii() and v.isdigit()):
            raise ValueError("page_id must be the advertiser's numeric page id")
        return v

    @model_validator(mode="after")
    def _page_id_matches_kind(self) -> Self:
        if self.kind == "advertiser" and self.page_id is None:
            raise ValueError("page_id is required for an advertiser search")
        if self.kind == "keyword" and self.page_id is not None:
            raise ValueError("page_id is only used by advertiser searches")
        return self


class ScrapeProgress(BaseModel):
    ads_found: int
    advertisers: int
    phase: Literal["opening", "scrolling", "parsing", "done"]
    message: str
