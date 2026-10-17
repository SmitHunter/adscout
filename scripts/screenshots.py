"""Capture AdScout GUI screenshots against a running local server.

Usage:
    # Your own data, for your own eyes (docs/screenshots/ is git-ignored):
    uv run adscout serve --no-open          # in another terminal
    uv run python scripts/screenshots.py --base http://127.0.0.1:8787 --out docs/screenshots/
    uv run python scripts/screenshots.py --snapshot 1 --hero   # also the full-page hero shot

    # The README media, from invented sample data only (committed to docs/media/):
    uv run adscout demo --no-open           # in another terminal
    uv run python scripts/screenshots.py --demo     # hero, angles, gallery, ... PNGs
    uv run python scripts/screenshots.py gif        # demo.gif: a ~20 s scripted tour

Own-data mode writes `{page}-{width}x{height}-{theme}.png` for `home`, `progress` (only while a
search is running), `report`, `report-angles` (only once the report has an angle analysis),
`report-creatives` and `report-advertisers`, at 1366x768 and 900x1200, in dark and light.
`--hero` adds `report-hero-dark.png`: the whole report, 1366 wide. It refuses to write into
docs/media/, which is for demo data only.

`--demo` and `gif` refuse anything but an `adscout demo` server, and check first that the demo
database names no real brand. The PNGs are taken at 1440x900 and device scale 2, then scaled
down to 1440 wide (crisp text) and kept under 600 KB. The GIF is 1200 wide at 10 fps, recorded
in virtual time (the page's clock only moves between frames), so the score's count-up is
captured frame by frame however slow the screenshots are.

Local only: it reads data from a server on this machine and never touches Meta. It never
starts an angle analysis either: POSTs to the analysis endpoint are blocked. Still shots are
captured with reduced motion, so the score and rulers are at rest, and the pointer is parked
off the page so no hover or tooltip state shows.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from PIL import Image
from playwright.async_api import Browser, Locator, Page, Route, async_playwright

from adscout.demo import DEMO_DATA_DIR, DEMO_PORT

Theme = Literal["dark", "light"]

THEMES: tuple[Theme, ...] = ("dark", "light")
VIEWPORTS: tuple[tuple[int, int], ...] = ((1366, 768), (900, 1200))
HERO_WIDTH = 1366
THEME_KEY = "adscout-theme"

OWN_DIR = Path("docs/screenshots")  # git-ignored: real ads never leave this machine
MEDIA_DIR = Path("docs/media")  # committed: demo data only

SHOT_SIZE = (1440, 900)
SHOT_SCALE = 2
MAX_PNG_BYTES = 600 * 1024

GIF_SIZE = (1200, 750)
GIF_FPS = 10
FRAME_MS = 1000 // GIF_FPS
MAX_GIF_BYTES = 8 * 1024 * 1024

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def loopback_base(value: str) -> str:
    """``--base``: an http(s) URL of a server on this machine, without a trailing slash.

    The script screenshots the user's own data; it never points a browser elsewhere.
    """
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        host = None
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or host not in LOOPBACK_HOSTS:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a local AdScout server. Use http://127.0.0.1:<port>."
        )
    return value.rstrip("/")


def is_media_dir(out: Path) -> bool:
    """Whether ``out`` is the committed docs/media folder (or inside it)."""
    media = MEDIA_DIR.resolve()
    target = out.resolve()
    return target == media or media in target.parents


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


async def settle(page: Page) -> None:
    """Fonts loaded, images decoded, pointer parked, nothing focused."""
    await page.evaluate("document.fonts.ready.then(() => true)")
    # Lazy images below the fold never load, so wait only on visible ones, and not forever.
    await page.evaluate(
        """() => Promise.race([
            new Promise((done) => setTimeout(done, 8000)),
            Promise.all(Array.from(document.images)
                .filter((img) => {
                    const box = img.getBoundingClientRect();
                    return !img.complete && box.bottom > 0 && box.top < innerHeight;
                })
                .map((img) => new Promise((done) => {
                    img.addEventListener('load', done, { once: true });
                    img.addEventListener('error', done, { once: true });
                }))),
        ])"""
    )
    await page.mouse.move(0, 0)
    await page.evaluate(
        "() => document.activeElement instanceof HTMLElement && document.activeElement.blur()"
    )
    await page.wait_for_timeout(250)


async def new_page(
    browser: Browser,
    theme: Theme,
    width: int,
    height: int,
    *,
    scale: int = 1,
    reduced_motion: bool = True,
) -> Page:
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=theme,
        reduced_motion="reduce" if reduced_motion else "no-preference",
        device_scale_factor=scale,
    )
    await context.add_init_script(
        f"try {{ localStorage.setItem('{THEME_KEY}', '{theme}'); }} catch (e) {{}}"
    )
    # Screenshots must never spend the user's Claude quota.
    await context.route("**/api/snapshots/*/analysis", block_analysis_runs)
    return await context.new_page()


async def block_analysis_runs(route: Route) -> None:
    if route.request.method == "POST":
        await route.abort()
    else:
        await route.continue_()


async def open_report(page: Page, base: str, snapshot: int) -> None:
    await page.goto(f"{base}/report/{snapshot}")
    await page.get_by_test_id("winner-score").wait_for()
    await settle(page)


async def open_angles(page: Page) -> bool:
    """Scroll the angles panel to the top; False when the report has no analysis yet."""
    if await page.get_by_test_id("analysis-verdict").count() == 0:
        return False
    await scroll_main(page, ANGLES)
    await settle(page)
    return True


async def open_tab(page: Page, name: str) -> None:
    await page.get_by_role("tab", name=name).click()
    await scroll_main(page, TABS)
    await settle(page)


# The shell scrolls inside <main>, so scroll that (scrollIntoView would also move the window).
ANGLES = "section:has(#angles-heading)"
TABS = "#report-tabs"
SCROLL_TARGET = """([selector, margin]) => {
    const main = document.querySelector('main');
    const el = document.querySelector(selector);
    if (!main || !el) return null;
    const box = el.getBoundingClientRect();
    // Stop short of the previous block's bottom edge, so no stray rule shows at the top.
    const prev = el.previousElementSibling?.getBoundingClientRect();
    const room = prev ? Math.min(margin, box.top - prev.bottom - 2) : margin;
    const top = main.scrollTop + box.top - main.getBoundingClientRect().top - room;
    return Math.max(0, Math.min(main.scrollHeight - main.clientHeight, Math.round(top)));
}"""
SET_SCROLL = "(top) => { document.querySelector('main').scrollTop = top; }"


async def scroll_main(page: Page, selector: str, margin: int = 24) -> None:
    top = await page.evaluate(SCROLL_TARGET, [selector, margin])
    if top is None:
        raise RuntimeError(f"Nothing to scroll to: {selector}")
    await page.evaluate(SET_SCROLL, top)


# --- Own data -------------------------------------------------------------------------------


async def capture_set(
    browser: Browser,
    base: str,
    out: Path,
    snapshot: int | None,
    theme: Theme,
    width: int,
    height: int,
    progress: bool,
) -> list[Path]:
    page = await new_page(browser, theme, width, height)
    written: list[Path] = []

    def target(name: str) -> Path:
        path = out / f"{name}-{width}x{height}-{theme}.png"
        written.append(path)
        return path

    try:
        await page.goto(f"{base}/")
        await page.get_by_role("search").wait_for()
        if progress:
            await page.get_by_role("heading", name="#", exact=False).first.wait_for()
            await settle(page)
            await page.screenshot(path=target("progress"))
        else:
            await settle(page)
            await page.screenshot(path=target("home"))

        if snapshot is not None:
            await open_report(page, base, snapshot)
            await page.screenshot(path=target("report"))
            if await open_angles(page):
                await page.screenshot(path=target("report-angles"))
            await open_tab(page, "Creatives")
            await page.screenshot(path=target("report-creatives"))
            await open_tab(page, "Advertisers")
            await page.screenshot(path=target("report-advertisers"))
    finally:
        await page.context.close()
    return written


async def capture_hero(browser: Browser, base: str, out: Path, snapshot: int) -> Path:
    """The whole report in one dark frame: the viewport grows to the page's height."""
    page = await new_page(browser, "dark", HERO_WIDTH, 900)
    try:
        await open_report(page, base, snapshot)
        # <main> scrolls inside the shell, so a full-page shot needs a tall viewport.
        height = await page.evaluate(
            "() => Math.ceil(document.querySelector('main')?.scrollHeight"
            " ?? document.body.scrollHeight)"
        )
        await page.set_viewport_size({"width": HERO_WIDTH, "height": int(height)})
        await page.evaluate(
            "() => document.querySelectorAll('img[loading=lazy]')"
            ".forEach((img) => { img.loading = 'eager'; })"
        )
        await settle(page)
        path = out / "report-hero-dark.png"
        await page.screenshot(path=path, full_page=True)
        return path
    finally:
        await page.context.close()


async def run_own(args: argparse.Namespace) -> int:
    base = args.base
    out = Path(args.out)
    if is_media_dir(out):
        print(
            f"{out} is for demo data only. Use --demo, or another --out for your own data.",
            file=sys.stderr,
        )
        return 2
    out.mkdir(parents=True, exist_ok=True)

    snapshots = get_json(f"{base}/api/snapshots")
    snapshot: int | None = args.snapshot
    if snapshot is None and snapshots:
        snapshot = int(snapshots[0]["id"])
    progress = get_json(f"{base}/api/searches/active") is not None

    written: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for theme in THEMES:
                for width, height in VIEWPORTS:
                    written += await capture_set(
                        browser, base, out, snapshot, theme, width, height, progress
                    )
            if args.hero and snapshot is not None:
                written.append(await capture_hero(browser, base, out, snapshot))
        finally:
            await browser.close()

    for path in written:
        print(path.as_posix())
    if snapshot is None:
        print("No snapshots yet: captured the home screen only.", file=sys.stderr)
    return 0


# --- Demo media -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoPlan:
    """What the demo media show, picked from the served data."""

    hero: int  # the strongest product's latest report
    query: str
    compare: tuple[int, ...]  # the strongest, the runner-up and the weakest
    watch: int  # the watch item tracking the hero's search


def check_demo(base: str, data_dir: Path) -> None:
    """Refuse anything but demo data, and any real brand in it."""
    from adscout.demo.generator import denylist_hits

    if get_json(f"{base}/api/settings").get("demo") is not True:
        raise SystemExit(f"{base} is not an AdScout demo server. Run uv run adscout demo.")
    db = data_dir / "adscout.db"
    if not db.is_file():
        raise SystemExit(f"No demo database at {db}. Run uv run adscout demo first.")
    hits = denylist_hits(db)
    if hits:
        listed = "\n  ".join(hits[:20])
        raise SystemExit(f"The demo data names a real brand; refusing to publish:\n  {listed}")


def demo_plan(base: str) -> DemoPlan:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in get_json(f"{base}/api/snapshots"):  # newest first
        if snapshot["kind"] == "keyword" and snapshot["status"] in ("ok", "capped"):
            latest.setdefault((snapshot["query"].lower(), snapshot["country"]), snapshot)
    if len(latest) < 2:
        raise SystemExit("The demo needs at least two finished searches.")
    headline = {
        int(s["id"]): int(get_json(f"{base}/api/snapshots/{s['id']}/report")["score"]["headline"])
        for s in latest.values()
    }
    ranked = sorted(latest.values(), key=lambda s: headline[int(s["id"])], reverse=True)
    hero = ranked[0]
    compare = tuple(dict.fromkeys(int(s["id"]) for s in (ranked[0], ranked[1], ranked[-1])))
    watch = next(
        (
            int(item["id"])
            for item in get_json(f"{base}/api/watch")
            if item["query"].lower() == hero["query"].lower()
        ),
        None,
    )
    if watch is None:
        raise SystemExit(f"No watch item tracks {hero['query']!r} in the demo.")
    return DemoPlan(hero=int(hero["id"]), query=hero["query"], compare=compare, watch=watch)


def save_png(image: Image.Image, path: Path) -> None:
    """Optimised PNG under MAX_PNG_BYTES: full colour if it fits, else a 256-colour palette."""
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    if buffer.tell() > MAX_PNG_BYTES:
        # No dithering: flat UI colours and text edges stay clean.
        quantized = image.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        buffer = io.BytesIO()
        quantized.save(buffer, "PNG", optimize=True)
    if buffer.tell() > MAX_PNG_BYTES:
        raise RuntimeError(f"{path.name} is {buffer.tell() // 1024} KB, over the 600 KB budget.")
    path.write_bytes(buffer.getvalue())


async def shoot(page: Page, path: Path) -> Path:
    """The viewport at device scale 2, scaled down to SHOT_SIZE."""
    await settle(page)
    raw = Image.open(io.BytesIO(await page.screenshot())).convert("RGB")
    save_png(raw.resize(SHOT_SIZE, Image.Resampling.LANCZOS), path)
    return path


async def demo_shots(browser: Browser, base: str, out: Path, plan: DemoPlan) -> list[Path]:
    width, height = SHOT_SIZE
    written: list[Path] = []
    page = await new_page(browser, "dark", width, height, scale=SHOT_SCALE)
    try:
        await open_report(page, base, plan.hero)
        await page.get_by_test_id("analysis-verdict").wait_for()
        written.append(await shoot(page, out / "hero.png"))
        await scroll_main(page, ANGLES)
        written.append(await shoot(page, out / "angles.png"))
        await scroll_main(page, TABS)
        await page.get_by_test_id("creative-tile").first.wait_for()
        written.append(await shoot(page, out / "gallery.png"))

        await page.goto(f"{base}/watch")
        await page.locator("main").get_by_role("link", name=plan.query, exact=True).wait_for()
        written.append(await shoot(page, out / "watchlist.png"))

        await page.goto(f"{base}/watch/{plan.watch}")
        await page.get_by_test_id("trend-readout").wait_for()
        written.append(await shoot(page, out / "trend.png"))

        await page.goto(f"{base}/compare?ids={','.join(map(str, plan.compare))}")
        await page.get_by_text("Winner Score").nth(len(plan.compare) - 1).wait_for()
        written.append(await shoot(page, out / "compare.png"))
    finally:
        await page.context.close()

    page = await new_page(browser, "light", width, height, scale=SHOT_SCALE)
    try:
        await open_report(page, base, plan.hero)
        await page.get_by_test_id("analysis-verdict").wait_for()
        written.append(await shoot(page, out / "light.png"))
    finally:
        await page.context.close()
    return written


# --- Demo GIF -------------------------------------------------------------------------------

# A drawn pointer: headless screenshots show no cursor, and the tour needs one to follow.
CURSOR_SCRIPT = """
addEventListener('DOMContentLoaded', () => {
  const el = document.createElement('div');
  el.id = '__tour-cursor';
  el.setAttribute('aria-hidden', 'true');
  el.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;'
    + 'transform:translate(-80px,-80px);transition:none;';
  el.innerHTML = '<span style="position:absolute;left:-14px;top:-14px;width:28px;height:28px;'
    + 'border-radius:50%;border:2px solid rgba(94,234,212,.9);opacity:0;"></span>'
    + '<svg width="20" height="24" viewBox="0 0 20 24" style="display:block">'
    + '<path d="M1.5 1.5v18.2l4.6-4.4 3 7 3.2-1.4-3-6.8h6.4z" fill="#fff" stroke="#0f172a"'
    + ' stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.body.appendChild(el);
  window.__tourCursor = (x, y, pressed) => {
    el.style.transform = `translate(${x}px, ${y}px)`;
    el.firstElementChild.style.opacity = pressed ? '1' : '0';
  };
});
"""


def ease(t: float) -> float:
    """Ease in and out: a pointer or a scroll starts and lands softly."""
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class Tour:
    """Records frames in virtual time: each frame moves the page's clock by FRAME_MS."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.frames: list[Image.Image] = []
        self.x, self.y = GIF_SIZE[0] * 0.55, GIF_SIZE[1] * 0.7
        self.pressed = False

    async def frame(self, count: int = 1) -> None:
        for _ in range(count):
            await self.page.clock.run_for(FRAME_MS)
            png = await self.page.screenshot()
            self.frames.append(Image.open(io.BytesIO(png)).convert("RGB"))

    async def hold(self, seconds: float) -> None:
        await self.frame(max(1, round(seconds * GIF_FPS)))

    async def wait(self, locator: Locator, seconds: float = 20) -> None:
        """Let real loading finish (fetches, lazy chunks) without recording it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while await locator.count() == 0:
            if loop.time() > deadline:
                raise TimeoutError(f"Timed out waiting for {locator}")
            await self.page.clock.run_for(16)
            await asyncio.sleep(0.02)

    async def until(self, locator: Locator, seconds: float = 3) -> None:
        """Record frames until ``locator`` shows (the wait is part of the story)."""
        for _ in range(round(seconds * GIF_FPS)):
            if await locator.count() > 0:
                return
            await self.frame()
        await self.wait(locator)

    async def point(self) -> None:
        await self.page.mouse.move(self.x, self.y)
        await self.page.evaluate(
            "([x, y, pressed]) => window.__tourCursor?.(x, y, pressed)",
            [self.x, self.y, self.pressed],
        )

    async def move(self, x: float, y: float, seconds: float = 0.6) -> None:
        x0, y0 = self.x, self.y
        steps = max(1, round(seconds * GIF_FPS))
        for i in range(1, steps + 1):
            t = ease(i / steps)
            self.x, self.y = x0 + (x - x0) * t, y0 + (y - y0) * t
            await self.point()
            await self.frame()

    async def click(self, locator: Locator, seconds: float = 0.6) -> None:
        box = await locator.bounding_box()
        if box is None or box["y"] < 0 or box["y"] + box["height"] > GIF_SIZE[1]:
            raise RuntimeError(f"Not on screen, can't click: {locator}")
        # Aim a little left of centre on wide targets, like a hand would.
        x = box["x"] + min(box["width"] / 2, 48)
        y = box["y"] + box["height"] / 2
        await self.move(x, y, seconds)
        self.pressed = True
        await self.point()
        await self.page.mouse.click(x, y)
        await self.frame(2)
        self.pressed = False
        await self.point()

    async def scroll(self, selector: str, seconds: float = 1.0, margin: int = 24) -> None:
        start = await self.page.evaluate("() => document.querySelector('main').scrollTop")
        target = await self.page.evaluate(SCROLL_TARGET, [selector, margin])
        if target is None:
            raise RuntimeError(f"Nothing to scroll to: {selector}")
        steps = max(1, round(seconds * GIF_FPS))
        for i in range(1, steps + 1):
            top = round(start + (target - start) * ease(i / steps))
            await self.page.evaluate(SET_SCROLL, top)
            await self.frame()


async def load_images(page: Page) -> None:
    """Load every image now, so tiles never pop in mid-tour."""
    await page.evaluate(
        "() => document.querySelectorAll('img[loading=lazy]')"
        ".forEach((img) => { img.loading = 'eager'; })"
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 20
    while await page.evaluate("() => Array.from(document.images).some((i) => !i.complete)"):
        if loop.time() > deadline:
            break
        await asyncio.sleep(0.05)


async def record_tour(browser: Browser, base: str, plan: DemoPlan) -> list[Image.Image]:
    """Home, a report counting up, angles, gallery, exclude/include, watchlist, trend, compare."""
    width, height = GIF_SIZE
    # Motion on: the count-up is the point of the second scene.
    page = await new_page(browser, "dark", width, height, reduced_motion=False)
    await page.context.add_init_script(CURSOR_SCRIPT)
    await page.clock.install()
    tour = Tour(page)
    main = page.locator("main")
    try:
        await page.goto(f"{base}/")
        await main.get_by_role("link", name=plan.query).first.wait_for()
        await settle(page)
        await page.clock.pause_at(await page.evaluate("Date.now()") + 1000)
        await tour.point()

        # 1. Home, then 2. a recent search opens the report as the score counts up.
        await tour.hold(0.8)
        await tour.click(main.get_by_role("link", name=plan.query).first, 0.7)
        await tour.wait(page.get_by_test_id("winner-score"))
        await tour.move(width * 0.62, height * 0.86, 0.3)
        await tour.hold(1.6)
        await load_images(page)

        # 3. The angles.
        await tour.scroll(ANGLES, 0.8)
        await tour.hold(1.6)

        # 4. The gallery, then 5. exclude an ad and include it again.
        await tour.scroll(TABS, 0.8)
        await tour.hold(0.5)
        tile = page.get_by_test_id("creative-tile").first
        days = (await tile.get_by_test_id("tile-days").inner_text()).strip()
        await tour.click(tile.get_by_role("button", name=re.compile(r"^Exclude")), 0.6)
        await tour.hold(0.8)
        await tour.click(page.get_by_role("tab", name=re.compile(r"^Excluded")), 0.5)
        excluded = page.get_by_test_id("creative-tile").filter(
            has=page.get_by_test_id("tile-days").get_by_text(days, exact=True)
        )
        await tour.until(excluded, 2)
        await tour.hold(0.3)
        await tour.click(excluded.first.get_by_role("button", name=re.compile(r"^Include")), 0.5)
        await tour.hold(0.8)

        # 6. The watchlist and a trend.
        await tour.click(page.get_by_role("link", name=re.compile(r"^Watchlist")), 0.7)
        watched = main.get_by_role("link", name=plan.query, exact=True)
        await tour.wait(watched)
        await tour.hold(0.8)
        await tour.click(watched, 0.5)
        await tour.wait(page.get_by_test_id("trend-readout"))
        # Park beside the title: over the charts the pointer would move the readout.
        await tour.move(width * 0.6, height * 0.09, 0.3)
        await tour.hold(1.4)

        # 7. Compare: tick three searches.
        await tour.click(page.get_by_role("link", name=re.compile(r"^Compare")), 0.7)
        await tour.hold(0.3)
        await tour.click(page.get_by_role("button", name=re.compile(r"^Choose searches")), 0.5)
        picker = page.get_by_role("dialog", name="Choose searches")
        await tour.wait(picker.get_by_role("checkbox").first)
        await tour.hold(0.2)
        queries = {int(s["id"]): s["query"] for s in get_json(f"{base}/api/snapshots")}
        for snapshot in plan.compare:
            # The newest run of each search is listed first.
            box = picker.get_by_role("checkbox", name=re.compile(re.escape(queries[snapshot])))
            await tour.click(box.first, 0.35)
            await tour.hold(0.1)
        await page.keyboard.press("Escape")
        await tour.wait(page.get_by_text("Winner Score").nth(len(plan.compare) - 1))
        await tour.frame()
        # Escape hands focus back to the trigger; no focus ring in the closing shot.
        await page.evaluate("() => document.activeElement?.blur?.()")
        await tour.move(width * 0.55, height * 0.97, 0.4)
        await tour.hold(2.0)
    finally:
        await page.context.close()
    return tour.frames


def save_gif(frames: list[Image.Image], path: Path) -> None:
    """One adaptive palette shared by every frame (no flicker), no dithering (crisp text)."""
    step = max(1, len(frames) // 24)
    sample = frames[::step]
    width, height = frames[0].size
    montage = Image.new("RGB", (width, height * len(sample)))
    for i, frame in enumerate(sample):
        montage.paste(frame, (0, height * i))
    palette = montage.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    indexed = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    # Identical neighbours merge into one longer frame; later frames store only what changed.
    indexed[0].save(
        path,
        save_all=True,
        append_images=indexed[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=False,
        disposal=1,
    )
    size = path.stat().st_size
    if size > MAX_GIF_BYTES:
        raise RuntimeError(f"{path.name} is {size / 1024 / 1024:.1f} MB, over the 8 MB budget.")


async def run_demo(args: argparse.Namespace) -> int:
    base = args.base or f"http://127.0.0.1:{DEMO_PORT}"
    out = Path(args.out) if args.out else MEDIA_DIR
    check_demo(base, Path(args.data_dir))
    plan = demo_plan(base)
    out.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            if args.command == "gif":
                frames = await record_tour(browser, base, plan)
                path = out / "demo.gif"
                save_gif(frames, path)
                written = [path]
                print(f"{len(frames)} frames, {len(frames) / GIF_FPS:.1f} s", file=sys.stderr)
            else:
                written = await demo_shots(browser, base, out, plan)
        finally:
            await browser.close()

    for path in written:
        print(f"{path.as_posix()}  {path.stat().st_size // 1024} KB")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("shots", "gif"),
        default="shots",
        help="shots (default) or gif: the demo tour (implies --demo)",
    )
    parser.add_argument(
        "--base",
        type=loopback_base,
        default=None,
        help="AdScout server URL, loopback only (default :8787, or :8788 with --demo)",
    )
    parser.add_argument(
        "--out", default=None, help="Output directory (docs/screenshots/, or docs/media/ for demo)"
    )
    parser.add_argument("--snapshot", type=int, default=None, help="Report to capture (newest)")
    parser.add_argument("--hero", action="store_true", help="Also capture report-hero-dark.png")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="README media from `adscout demo` (invented data) into docs/media/",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEMO_DATA_DIR),
        help="The demo data folder, checked for real brand names (default data-demo)",
    )
    args = parser.parse_args()
    if args.demo or args.command == "gif":
        sys.exit(asyncio.run(run_demo(args)))
    args.base = args.base or "http://127.0.0.1:8787"
    args.out = args.out or str(OWN_DIR)
    sys.exit(asyncio.run(run_own(args)))


if __name__ == "__main__":
    main()
