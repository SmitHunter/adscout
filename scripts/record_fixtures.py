"""Record sanitised Meta Ad Library fixtures (Task 3 recon).

Usage:
    uv run python scripts/record_fixtures.py "posture corrector" --country US --scrolls 3
    uv run python scripts/record_fixtures.py --login   # sign in once, close the window when done

Politeness: one search per invocation, at most 3 scrolls, 3-6 s random waits, headful.
Never automates login, CAPTCHAs or checkpoints: if one is detected the page is saved as a
block fixture and the script exits with code 2.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from playwright.async_api import BrowserContext, Page, Request, Response, async_playwright

from adscout.config import get_settings
from adscout.models import SearchQuery
from adscout.sources.meta_web.blocks import detect_block
from adscout.sources.meta_web.driver import launch_context, open_until_closed
from adscout.sources.meta_web.urls import LIBRARY_URL, build_library_url

FIXTURES_DIR = Path("tests/fixtures/meta_web")
MAX_SCROLLS = 3

# Keys whose values identify the session / viewer. Advertiser data is left alone.
SECRET_KEYS = (
    "fb_dtsg",
    "lsd",
    "jazoest",
    "__user",
    "av",
    "actorID",
    "USER_ID",
    "ACCOUNT_ID",
    "NAME",
    "SHORT_NAME",
    "viewerID",
    "token",
    "async_get_token",
    "dtsg",
    "__hsi",
    "__spin_r",
    "__spin_t",
    "__rev",
    "hsi",
    "client_revision",
    "server_revision",
    "c_user",
    "xs",
    "datr",
    "fr",
    "sb",
    "sessionID",
)
_KEYS = "|".join(re.escape(k) for k in SECRET_KEYS)
# "key":"value" / "key":123   (plain JSON)
_JSON_RE = re.compile(rf'"({_KEYS})"(\s*:\s*)("(?:[^"\\]|\\.)*"|-?\d+)')
# \"key\":\"value\"  (JSON embedded in an escaped string)
_ESC_JSON_RE = re.compile(rf'\\"({_KEYS})\\"(\s*:\s*)(\\"(?:[^"\\]|\\\\.)*?\\"|-?\d+)')
# key=value in URLs / form bodies
_FORM_RE = re.compile(rf"(?<![A-Za-z0-9_])({_KEYS})=([^&\"'\s<>]*)")
# ["DTSGInitialData",[],{"token":"..."}] style handled by "token" key above.


def sanitise(text: str) -> str:
    text = _JSON_RE.sub(lambda m: f'"{m.group(1)}"{m.group(2)}"REDACTED"', text)
    text = _ESC_JSON_RE.sub(lambda m: f'\\"{m.group(1)}\\"{m.group(2)}\\"REDACTED\\"', text)
    text = _FORM_RE.sub(lambda m: f"{m.group(1)}=REDACTED", text)
    return text


_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def shrink_html(html: str) -> str:
    """Drop <style> blocks and large inline scripts that carry no ad data."""
    html = _STYLE_RE.sub("", html)

    def _script(m: re.Match[str]) -> str:
        body = m.group(1)
        if "ad_archive_id" in body or "search_results_connection" in body or len(body) < 2000:
            return m.group(0)
        return ""

    return _SCRIPT_RE.sub(_script, html)


def slugify(keyword: str, country: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    return f"{slug}-{country.upper()}"


def summarise_post_data(post: str | None) -> dict[str, Any]:
    """Keep only the non-secret GraphQL request fields (name, doc_id, variables)."""
    if not post:
        return {}
    form = parse_qs(post)
    out: dict[str, Any] = {}
    for key in ("fb_api_req_friendly_name", "doc_id"):
        if key in form:
            out[key] = form[key][0]
    if "variables" in form:
        try:
            out["variables"] = json.loads(form["variables"][0])
        except json.JSONDecodeError:
            out["variables"] = form["variables"][0]
    return json.loads(sanitise(json.dumps(out)))


def strip_query(url: str) -> str:
    """Keep the Ad Library document URL whole; drop query strings (session state) elsewhere."""
    if url.startswith(LIBRARY_URL):
        return sanitise(url)
    return re.sub(r"__rd_verify_[\w-]+", "__rd_verify_REDACTED", url.split("?", 1)[0])


def block_reason(url: str, inner_text: str) -> str | None:
    """The app's own block detector, reduced to its reason string for meta.json."""
    blocked = detect_block(url, inner_text)
    return blocked.reason if blocked is not None else None


class Recorder:
    def __init__(self) -> None:
        self.graphql: list[dict[str, Any]] = []
        self.response_urls: list[str] = []
        self._tasks: list[asyncio.Task[None]] = []

    def on_response(self, response: Response) -> None:
        url = response.url
        if "facebook.com" in url and response.request.resource_type in ("document", "xhr", "fetch"):
            self.response_urls.append(strip_query(url))
        if "/api/graphql" in url:
            self._tasks.append(asyncio.create_task(self._capture(response)))

    async def _capture(self, response: Response) -> None:
        try:
            text = await response.text()
        except Exception as exc:  # body may be gone (redirect / navigation)
            print(f"  ! could not read body of {response.url}: {exc}", file=sys.stderr)
            return
        if "ad_archive_id" not in text and "search_results_connection" not in text:
            return
        req: Request = response.request
        self.graphql.append(
            {
                "url": sanitise(response.url),
                "status": response.status,
                "request": summarise_post_data(req.post_data),
                "body": text,
                "seen_at": datetime.now(UTC).isoformat(),
            }
        )

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks)
            self._tasks.clear()


async def launch(pw: Any) -> BrowserContext:
    ctx: BrowserContext = await launch_context(pw, get_settings().browser_profile_dir, False)
    return ctx


async def login_mode() -> int:
    print("Sign in in the browser window, then close it to finish.")
    await open_until_closed(get_settings().browser_profile_dir, LIBRARY_URL)
    return 0


async def polite_wait() -> None:
    await asyncio.sleep(random.uniform(3.0, 6.0))


async def record(keyword: str, country: str, scrolls: int, out_root: Path) -> int:
    scrolls = max(0, min(scrolls, MAX_SCROLLS))
    query = SearchQuery(keyword=keyword, country=country)
    url = build_library_url(query)
    slug = slugify(keyword, query.country)
    rec = Recorder()

    async with async_playwright() as pw:
        ctx = await launch(pw)
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.on("response", rec.on_response)
        print(f"-> {url}")
        await page.goto(url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=15000)
        await polite_wait()

        html_raw = await page.content()
        inner = await page.evaluate("document.body ? document.body.innerText : ''")
        block = block_reason(page.url, inner)
        scrolls_done = 0
        if block is None:
            for i in range(scrolls):
                print(f"  scroll {i + 1}/{scrolls}")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await polite_wait()
                scrolls_done += 1
            inner = await page.evaluate("document.body ? document.body.innerText : ''")
            block = block_reason(page.url, inner)
        await rec.drain()
        final_url = page.url
        await ctx.close()

    embedded_ids = len(re.findall(r"ad_archive_id", html_raw))
    out_dir = (out_root / "blocks" / slug) if block else (out_root / slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "page.html").write_text(sanitise(shrink_html(html_raw)), encoding="utf-8")
    (out_dir / "inner_text.txt").write_text(sanitise(inner), encoding="utf-8")

    files: list[dict[str, Any]] = []
    for n, item in enumerate(rec.graphql, start=1):
        name = f"graphql_{n}.json"
        body = sanitise(item["body"])
        (out_dir / name).write_text(body, encoding="utf-8")
        files.append(
            {
                "file": name,
                "url": item["url"],
                "status": item["status"],
                "request": item["request"],
                "bytes": len(body.encode("utf-8")),
                "ad_archive_id_mentions": body.count('"ad_archive_id"'),
            }
        )

    meta = {
        "keyword": keyword,
        "country": query.country,
        "search_url": url,
        "final_url": sanitise(final_url),
        "recorded_at": datetime.now(UTC).isoformat(),
        "scrolls_requested": scrolls,
        "scrolls_done": scrolls_done,
        "blocked": block,
        "html_ad_archive_id_mentions": embedded_ids,
        "graphql_files": files,
        "response_urls": rec.response_urls,
    }
    (out_dir / "meta.json").write_text(sanitise(json.dumps(meta, indent=2)), encoding="utf-8")

    print(f"saved {out_dir} ({len(files)} graphql files, html mentions={embedded_ids})")
    if block:
        print(f"BLOCKED ({block}). Sign in once with:")
        print("  uv run python scripts/record_fixtures.py --login")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keyword", nargs="?", help="search keyword")
    parser.add_argument("--country", default="US")
    parser.add_argument("--scrolls", type=int, default=3)
    parser.add_argument("--out", type=Path, default=FIXTURES_DIR)
    parser.add_argument("--login", action="store_true", help="open the profile to sign in")
    args = parser.parse_args()
    if args.login:
        return asyncio.run(login_mode())
    if not args.keyword:
        parser.error("keyword is required unless --login is given")
    return asyncio.run(record(args.keyword, args.country, args.scrolls, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
