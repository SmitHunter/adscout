from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC in SQLite; returns tz-aware UTC. Rejects naive input."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def _utc_col(nullable: bool = False) -> Any:
    return Column(UTCDateTime(), nullable=nullable)


class WatchItem(SQLModel, table=True):
    """A search re-run daily (M5): a keyword, or an advertiser's page (``page_id``)."""

    __tablename__ = "watchitem"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = "keyword"  # "keyword" | "advertiser"
    query: str  # the keyword, or the advertiser's display name
    page_id: str | None = None
    country: str
    active_status: str = "active"
    media_type: str = "all"
    enabled: bool = True
    created_at: datetime = Field(sa_column=_utc_col())
    last_run_at: datetime | None = Field(default=None, sa_column=_utc_col(nullable=True))
    last_status: str | None = None


class Snapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # For an advertiser snapshot: the advertiser's display name.
    query: str = Field(index=True)
    kind: str = "keyword"
    page_id: str | None = None  # advertiser snapshots only
    country: str
    active_status: str
    media_type: str
    source: str
    started_at: datetime = Field(sa_column=_utc_col())
    finished_at: datetime | None = Field(default=None, sa_column=_utc_col(nullable=True))
    status: str = "running"
    error_message: str | None = None
    ad_count: int = 0
    advertiser_count: int = 0
    # The watch item a scheduled re-check ran for (M5); None for manual searches.
    watch_item_id: int | None = Field(default=None, foreign_key="watchitem.id", index=True)


class AdRow(SQLModel, table=True):
    __tablename__ = "adrow"
    __table_args__ = (UniqueConstraint("snapshot_id", "ad_archive_id"),)

    id: int | None = Field(default=None, primary_key=True)
    snapshot_id: int = Field(foreign_key="snapshot.id", index=True)
    source: str
    ad_archive_id: str
    page_id: str
    page_name: str
    start_date: datetime = Field(sa_column=_utc_col())
    end_date: datetime | None = Field(default=None, sa_column=_utc_col(nullable=True))
    is_active: bool
    body: str = ""
    title: str | None = None
    link_url: str | None = None
    link_caption: str | None = None
    cta_text: str | None = None
    display_format: str | None = None
    media: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    collation_id: str | None = None
    collation_count: int = 1
    platforms: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    relevant: bool | None = None  # reserved for M2
    creative_key: str | None = None  # reserved for M2


class Analysis(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("snapshot_id", "prompt_version", "model", "input_hash"),)

    id: int | None = Field(default=None, primary_key=True)
    snapshot_id: int = Field(foreign_key="snapshot.id", index=True)
    prompt_version: str
    model: str
    backend: str
    input_hash: str
    ad_count: int
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    usage: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: datetime = Field(sa_column=_utc_col())


class Exclusion(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("keyword_norm", "scope", "target_id"),)

    id: int | None = Field(default=None, primary_key=True)
    keyword_norm: str = Field(index=True)
    keyword_raw: str = ""
    scope: str
    target_id: str
    mode: str
    created_at: datetime = Field(sa_column=_utc_col())


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    watch_item_id: int = Field(foreign_key="watchitem.id", index=True)
    snapshot_id: int | None = Field(default=None, foreign_key="snapshot.id")
    kind: str  # "change" | "blocked"
    change_pct: float | None = None
    message: str
    created_at: datetime = Field(sa_column=_utc_col())
    read: bool = False
