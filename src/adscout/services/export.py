"""Snapshot exports: a CSV of every ad, and PDF/PNG renders of the report's print page.

The PDF/PNG come from a fresh, non-persistent headless Chromium that opens the app's own
``/report/{id}/print`` route. It never touches the persistent Meta browser profile, so it
needs no profile lock and can run while a search is scraping.
"""

import csv
import io
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Literal
from zoneinfo import ZoneInfo

from adscout.services.report import AdView, Report

ExportFormat = Literal["csv", "pdf", "png"]
RenderFormat = Literal["pdf", "png"]
ReportRenderer = Callable[[str, int, RenderFormat], Awaitable[bytes]]

CSV_COLUMNS = (
    "ad_archive_id",
    "advertiser",
    "page_id",
    "start_date",
    "end_date",
    "days_running",
    "is_active",
    "relevant",
    "reason",
    "creative_key",
    "collation_count",
    "title",
    "body",
    "link_url",
    "landing_domain",
    "media_types",
)
BOM = "﻿"
RENDER_TIMEOUT_S = 30.0
PNG_WIDTH = 1600
PRINT_READY = "[data-print-ready]"
PRINT_FAILED_ATTR = "data-print-failed"
# The print page sets one of these on <body>: ready to print, or why it can't be.
PRINT_SETTLED = f"body[data-print-ready], body[{PRINT_FAILED_ATTR}]"
PrintFailure = Literal["not-found", "load"]
SLUG_MAX = 40
CHROMIUM_MISSING = "Chromium for exports is not installed. Run: uv run playwright install chromium"

# A cell starting with one of these is read as a formula by Excel/Sheets (CSV injection).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class ExportError(Exception):
    """The renderer could not produce the file (e.g. Chromium is not installed)."""


class ExportTimeout(ExportError, TimeoutError):
    """The print page did not become ready in time."""


class ExportPageFailed(ExportError):
    """The print page reported it could not show the report (``data-print-failed``)."""

    def __init__(self, reason: PrintFailure) -> None:
        super().__init__(f"The print page could not show the report ({reason}).")
        self.reason: PrintFailure = reason


def escape_cell(value: str) -> str:
    """Prefix ``'`` to a value a spreadsheet would run as a formula."""
    return "'" + value if value.startswith(_FORMULA_START) else value


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NOT_SLUG.sub("-", ascii_text.lower()).strip("-")[:SLUG_MAX].strip("-") or "snapshot"


def export_filename(query: str, snapshot_id: int, fmt: ExportFormat | str) -> str:
    return f"adscout-{slug(query)}-{snapshot_id}.{fmt}"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _row(ad: AdView, tz: ZoneInfo) -> list[str]:
    media_types = list(dict.fromkeys(m.type for m in ad.media))
    cells = [
        ad.ad_archive_id,
        ad.page_name,
        ad.page_id,
        ad.start_date.astimezone(tz).date().isoformat(),
        ad.end_date.astimezone(tz).date().isoformat() if ad.end_date else "",
        str(ad.days_running),
        _bool(ad.is_active),
        _bool(ad.relevant),
        ad.reason,
        ad.creative_key,
        str(ad.collation_count),
        ad.title or "",
        ad.body,
        ad.link_url or "",
        ad.landing_domain or "",
        ";".join(media_types),
    ]
    return [escape_cell(c) for c in cells]


def snapshot_csv(report: Report, tz: ZoneInfo) -> bytes:
    """One row per ad (relevant and excluded, with the reason), UTF-8 with a BOM for Excel.

    Dates are ISO dates in ``tz``.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    writer.writerows(_row(ad, tz) for ad in report.ads)
    return (BOM + out.getvalue()).encode("utf-8")


async def render_url(url: str, fmt: RenderFormat, *, timeout_s: float = RENDER_TIMEOUT_S) -> bytes:
    """Open ``url`` in a fresh headless Chromium, wait for ``[data-print-ready]``, and
    return an A4 PDF (one page when the sheet fits) or a 1600 px wide full-page PNG.

    Raises ``ExportPageFailed`` as soon as the page sets ``data-print-failed`` instead,
    ``ExportTimeout`` past ``timeout_s`` and ``ExportError`` on any browser failure.
    """
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    from playwright.async_api import async_playwright

    timeout_ms = timeout_s * 1000
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": PNG_WIDTH, "height": 1000})
                page.set_default_timeout(timeout_ms)
                await page.goto(url, wait_until="load")
                body = await page.wait_for_selector(PRINT_SETTLED, state="attached")
                failed = None if body is None else await body.get_attribute(PRINT_FAILED_ATTR)
                if failed is not None:
                    # Never a PDF/PNG of the error page.
                    raise ExportPageFailed("not-found" if failed == "not-found" else "load")
                if fmt == "pdf":
                    # No page_ranges: the print page zooms its sheet onto one page, and
                    # anything that still overflows goes to page 2 instead of being cut.
                    return await page.pdf(format="A4", print_background=True)
                return await page.screenshot(full_page=True, type="png")
            finally:
                await browser.close()
    except PlaywrightTimeout as exc:
        raise ExportTimeout("Export timed out.") from exc
    except PlaywrightError as exc:
        text = str(exc).strip()
        if "Executable doesn't exist" in text:
            raise ExportError(CHROMIUM_MISSING) from exc
        first_line = text.splitlines()[0] if text else repr(exc)
        raise ExportError(f"Export failed: {first_line}") from exc


async def render_report(base_url: str, snapshot_id: int, fmt: RenderFormat) -> bytes:
    """Render the app's ``/report/{id}/print`` page served at ``base_url``."""
    return await render_url(f"{base_url.rstrip('/')}/report/{snapshot_id}/print", fmt)
