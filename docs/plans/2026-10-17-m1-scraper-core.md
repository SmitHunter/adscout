# AdScout M1 — Scraper Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run adscout scrape "posture corrector" --country US` stores normalised ads from the Meta Ad Library in `data/adscout.db`, with parser tests running offline against recorded fixtures.

**Architecture:** A `Source` protocol returns normalised `Ad` models. `MetaWebSource` drives Chromium through a thin `PageDriver` wrapper, captures GraphQL responses (primary) or `innerText` (fallback), and stops politely. Parsers are pure functions over strings, so they are tested only against fixtures. Persistence uses SQLModel on SQLite. The CLI uses Typer + Rich.

**Tech Stack:** Python 3.12, uv, Playwright (Chromium), Pydantic v2, pydantic-settings, SQLModel, Typer, Rich, httpx, pytest, pytest-asyncio, respx, ruff, mypy (strict).

**Spec:** `docs/superpowers/specs/2026-10-17-adscout-design.md` (and `docs/build-prompt.md`)

## Global Constraints

- Python `>=3.12,<3.13`, managed by `uv`. The console script is `adscout = "adscout.cli:app"`.
- Source layout: `src/adscout/`. Tests: `tests/`. Fixtures: `tests/fixtures/`.
- mypy `strict = true` on `src/`, and ruff (`E,F,I,UP,B,SIM,RUF`) must be clean in every task.
- Tests never touch the live network. Playwright is only used inside `MetaWebSource` and `scripts/record_fixtures.py`.
- All datetimes are timezone-aware UTC. Meta dates (unix seconds or `Mon D, YYYY` text) are converted to UTC.
- Politeness defaults: cap 300 ads, 2–5 s randomised scroll delay, stop after 3 scrolls that add nothing new, one search at a time (process-wide `asyncio.Lock`).
- Never automate CAPTCHAs or login. On a wall, raise `SourceBlocked`.
- Browser profile: `data/browser-profile/`. DB: `data/adscout.db`. `data/` is gitignored.
- Search URL: `https://www.facebook.com/ads/library/?active_status={active|all}&ad_type=all&country={CC}&q={urlencoded}&search_type=keyword_unordered&media_type={all|image|video|meme}`
- Commit after every task, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Keyword with quotes, `&`, `#` or non-ASCII** (`"knee brace" & sleeve`, `crème`): it must be URL-encoded, not break the URL. Test in Task 2.
2. **Zero results:** the snapshot is saved with `status="ok"`, `ad_count=0`, and the CLI prints `No ads found for "<kw>" in <CC>. Try a broader keyword.` Tests in Tasks 8 and 9.
3. **Carousel/DCO ads with no top-level body, a missing `start_date`, or `{{product.name}}` template text:** the parser takes the body from the first card, skips ads without a start date (with a logged warning) and never crashes. Test in Task 4.
4. **A login wall or checkpoint mid-scroll:** ads gathered so far are kept, the snapshot gets `status="blocked"`, and the CLI prints the reason plus `Run: uv run adscout browser` to sign in. Tests in Tasks 6, 8 and 9.
5. **Re-running the same search:** it creates a new snapshot (history is preserved), and duplicate `ad_archive_id`s within one run are collapsed. Tests in Tasks 7 and 8.

---

### Task 1: Project scaffold, settings, CLI skeleton

**Files:**
- Create: `pyproject.toml`, `.python-version` (`3.12`), `.env.example`, `src/adscout/__init__.py` (`__version__ = "0.1.0"`), `src/adscout/config.py`, `src/adscout/cli.py`, `tests/conftest.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, env prefix none, `.env` file) with `data_dir: Path = Path("data")`, `db_path` (property → `data_dir / "adscout.db"`), `browser_profile_dir` (property → `data_dir / "browser-profile"`), `max_results: int = 300`, `scroll_delay_min: float = 2.0`, `scroll_delay_max: float = 5.0`, `headless: bool = False`, `meta_access_token: SecretStr | None = None`, `anthropic_api_key: SecretStr | None = None`, `timezone: str = "Australia/Melbourne"`; `get_settings() -> Settings` (lru_cached). `app: typer.Typer` in `cli.py` with a `--version` callback.

- [ ] **Step 1:** `uv python install 3.12`, then `uv init --package --name adscout` (adjust to src layout) and `uv add pydantic pydantic-settings sqlmodel typer rich httpx playwright`, plus `uv add --dev pytest pytest-asyncio respx ruff mypy`. Configure `[tool.pytest.ini_options] asyncio_mode = "auto"`, ruff and mypy in `pyproject.toml`. `.env.example` lists `META_ACCESS_TOKEN=`, `ANTHROPIC_API_KEY=`, `HEADLESS=false` with comments.
- [ ] **Step 2: Failing tests** in `tests/test_cli.py`: `test_version_prints` (CliRunner `["--version"]` → exit 0, output contains `0.1.0`); `test_settings_defaults` (`Settings(_env_file=None).max_results == 300` and `.db_path == Path("data/adscout.db")`).
- [ ] **Step 3:** `uv run pytest -q`. Expected: FAIL (import errors).
- [ ] **Step 4:** Implement `config.py` and `cli.py`.
- [ ] **Step 5:** `uv run pytest -q && uv run ruff check . && uv run mypy src`. Expected: all pass.
- [ ] **Step 6:** Commit `chore: scaffold adscout package, settings and CLI`.

### Task 2: Domain models and the Source protocol

**Files:**
- Create: `src/adscout/models/__init__.py`, `src/adscout/models/ad.py`, `src/adscout/sources/__init__.py`, `src/adscout/sources/base.py`, `src/adscout/sources/meta_web/__init__.py`, `src/adscout/sources/meta_web/urls.py`
- Test: `tests/models/test_ad.py`, `tests/sources/test_base.py`

**Interfaces:**
- Produces (`models/ad.py`):
  - `MediaType = Literal["image", "video"]`; `MediaItem(BaseModel)`: `type: MediaType`, `url: str`, `preview_url: str | None = None`, `local_path: str | None = None`, `phash: str | None = None`
  - `Ad(BaseModel)`: `source: str`, `ad_archive_id: str`, `page_id: str`, `page_name: str`, `start_date: datetime`, `end_date: datetime | None`, `is_active: bool`, `body: str = ""`, `title: str | None`, `link_url: str | None`, `link_caption: str | None`, `cta_text: str | None`, `display_format: str | None`, `media: list[MediaItem] = []`, `collation_id: str | None`, `collation_count: int = 1`, `platforms: list[str] = []`, `raw: dict[str, Any] = {}`. Property `days_running(now: datetime) -> int` (for inactive ads: end − start).
  - `SearchQuery(BaseModel)`: `keyword: str` (stripped, 1–100 chars), `country: str = "US"` (2 upper letters, or `"EU"` meaning `ALL` EU members via API only), `active_status: Literal["active", "all"] = "active"`, `media_type: Literal["all", "image", "video", "meme"] = "all"`, `limit: int = 300` (1–1000).
  - `ScrapeProgress(BaseModel)`: `ads_found: int`, `advertisers: int`, `phase: Literal["opening", "scrolling", "parsing", "done"]`, `message: str`
- Produces (`sources/base.py`): `ProgressCallback = Callable[[ScrapeProgress], Awaitable[None] | None]`; `CancelToken` (`cancel()`, `cancelled: bool`); `class SourceBlocked(Exception)` with `reason: str`, `how_to_fix: str`; `class SourceError(Exception)`; `class Source(Protocol)`: `name: str`, `def supports(self, q: SearchQuery) -> bool`, `async def search(self, q: SearchQuery, progress: ProgressCallback | None = None, cancel: CancelToken | None = None) -> SearchResult`; `SearchResult(BaseModel)`: `ads: list[Ad]`, `status: Literal["ok", "blocked", "cancelled", "capped"]`, `message: str | None = None`. Also `build_library_url(q: SearchQuery) -> str` in `sources/meta_web/urls.py` (create the `meta_web` package here).

- [ ] **Step 1: Failing tests:** `test_keyword_is_stripped`, `test_country_must_be_two_letters` (`"USA"` raises ValidationError), `test_days_running_active_vs_ended` (active ad started 2026-04-01 at now 2026-10-17 → 199; ended ad 2026-01-01→2026-01-31 → 30), `test_build_library_url_encodes_keyword` (`'"knee brace" & sleeve'` produces a URL whose parsed `q` query param round-trips exactly, `country=US`, `search_type=keyword_unordered`), `test_cancel_token`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. `build_library_url` uses `urllib.parse.urlencode`.
- [ ] **Step 4:** Run pytest, ruff and mypy. Expected: PASS.
- [ ] **Step 5:** Commit `feat: add Ad model, SearchQuery and Source protocol`.

### Task 3: Recon — record live fixtures (controller runs this, with the user watching)

**Files:**
- Create: `scripts/record_fixtures.py`, `tests/fixtures/meta_web/README.md`, `tests/fixtures/meta_web/<slug>/{graphql_*.json, page.html, inner_text.txt, meta.json}`

**Interfaces:**
- Consumes: `build_library_url`, `Settings.browser_profile_dir`
- Produces: fixture folders for `posture-corrector-US` and `heated-eye-mask-US` (2 searches total; 3 scrolls each), plus one deliberately empty search (`zzqxwv-nothing-US`). A `README.md` documents the observed payload shape: response URL pattern, JSON path to the ad nodes, field names, date format, the pagination cursor, and whether page-1 ads arrive embedded in HTML `<script type="application/json">` instead of XHR.

- [ ] **Step 1:** Write the script: launch a persistent context (headful) on the profile, `page.on("response")` save the body of every response whose URL contains `/api/graphql` and whose text contains `ad_archive_id`, save `page.content()`, `document.body.innerText` and response URLs. **Sanitise before writing**: drop request headers and cookies, and regex-replace `fb_dtsg`, `lsd`, `jazoest`, `__user`, `av` and any `"actorID"`/`"USER_ID"` value with `"REDACTED"`.
- [ ] **Step 2:** Run `uv run python scripts/record_fixtures.py "posture corrector" --country US --scrolls 3` (and the other two). Check: ≥ 1 graphql file containing `ad_archive_id`, OR document that page 1 is embedded in HTML. If it hits a login wall when logged out, record that fact in the README (it also becomes a block-detector fixture), stop, and ask the user to sign in via the headful window.
- [ ] **Step 3:** Inspect the payloads and write the shape notes in `README.md`. **Update Tasks 4–5 field paths in this plan if they differ from the expected shape** (`data.ad_library_main.search_results_connection.edges[].node.collated_results[]`, fields `ad_archive_id, collation_id, collation_count, page_id, page_name, start_date (unix s), end_date, is_active, publisher_platform, snapshot{body{text}, title, link_url, caption, cta_text, display_format, images[{original_image_url,resized_image_url}], videos[{video_hd_url,video_sd_url,video_preview_image_url}], cards[]}`).
- [ ] **Step 4:** `git grep -nE "fb_dtsg\"?:\s*\"[^R]|c_user|xs="` over the fixtures. Expected: no output.
- [ ] **Step 5:** Commit `test: record sanitised Meta Ad Library fixtures`.

### Task 4: GraphQL / embedded-JSON parser

**Files:**
- Create: `src/adscout/sources/meta_web/parse_graphql.py`
- Test: `tests/sources/meta_web/test_parse_graphql.py`

**Interfaces:**
- Produces: `iter_json_documents(text: str) -> Iterator[Any]` (strips `for (;;);`, splits concatenated/newline-delimited JSON); `extract_embedded_json(html: str) -> list[Any]` (all `<script type="application/json">` bodies containing `ad_archive_id`); `find_ad_nodes(doc: Any) -> Iterator[dict[str, Any]]` (recursive walk that yields every dict with both `ad_archive_id` and `snapshot`, so it is robust to path changes); `node_to_ad(node: dict[str, Any], source: str = "meta_web") -> Ad | None`; `parse_payloads(texts: Iterable[str]) -> list[Ad]` (dedup by `ad_archive_id`, first seen wins).

- [ ] **Step 1: Failing tests** (fixture-driven; exact numbers come from Task 3 fixtures and are written into the tests): `test_parses_all_ads_in_fixture` (count equals the number of unique `ad_archive_id`s found by a plain regex over the fixture), `test_known_ad_fields` (one hand-checked ad: id, page_name, start_date as UTC, collation_count, first media URL), `test_carousel_body_falls_back_to_first_card`, `test_missing_start_date_skipped` (synthetic node), `test_template_body_kept_raw` (`{{product.name}}` survives unchanged), `test_for_loop_prefix_and_concatenated_json`, `test_embedded_html_payload` (`page.html` fixture).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. `start_date` unix seconds → `datetime.fromtimestamp(x, UTC)`. Media: images first, then videos (`url=video_hd_url or video_sd_url`, `preview_url=video_preview_image_url`), and from cards when top-level media is empty. `raw` = the node.
  - *Recon (Task 3) shape notes — see `tests/fixtures/meta_web/README.md`:* GraphQL bodies are single plain JSON docs (no `for (;;);`) but keep `iter_json_documents` tolerant. Page 1 (30 ads, plus `count`) is embedded at `require[0][3][0].__bbox.require[0][3][1].__bbox.result.data.ad_library_main.search_results_connection` in a `<script type="application/json">`; XHR pages are `data.ad_library_main.search_results_connection` (no `count`). `collation_count` and `collation_id` can be `null` (→ 1 / None); `snapshot.body` can be `null` (→ `""`); `snapshot.caption` → `link_caption`; `start_date` is always 07:00 UTC (midnight US Pacific); `end_date` is set **even for active ads** (last delivery day) — keep it, `days_running` already uses `now` for active ads. `DCO`/`DPA` ads have empty top-level media and template body/title. The "unique ids by regex" count should regex `"ad_archive_id":"(\d+)"` (expected: posture 30 html + 39 graphql = 69, heated 30 + 60 = 90).
- [ ] **Step 4:** Run pytest, ruff and mypy. Expected: PASS.
- [ ] **Step 5:** Commit `feat: parse Ad Library GraphQL payloads into Ads`.

### Task 5: innerText fallback parser

**Files:**
- Create: `src/adscout/sources/meta_web/parse_text.py`
- Test: `tests/sources/meta_web/test_parse_text.py`

**Interfaces:**
- Produces: `parse_inner_text(text: str, source: str = "meta_web_text") -> list[Ad]`. Split on `"Library ID:"`. Per chunk: the id is the first token, `Started running on (\d{1,2} \w{3} \d{4}|\w{3} \d{1,2}, \d{4})` → UTC midnight (recon: the live page uses `31 Oct 2024`; accept both forms), advertiser = the line before `Sponsored`, `(\d+) ads use this creative` → `collation_count` (default 1), body = the lines after `Sponsored` up to the next known label. `is_active`: the status line (`Active`/`Inactive`) comes immediately **before** `Library ID:`, so read it from the last non-empty line of the preceding text, not the chunk itself. `page_id = ""` is allowed (the text has none), and the parser sets `raw={"text": chunk}`.

- [ ] **Step 1: Failing tests** on `inner_text.txt` from the fixtures: count matches the number of `Library ID:` occurrences that have a start date; one hand-checked ad (id, advertiser, date, collation_count); `test_chunk_without_date_skipped`.
- [ ] **Step 2–4:** Run (FAIL), implement, then run pytest, ruff and mypy (PASS).
- [ ] **Step 5:** Commit `feat: add innerText fallback parser`.

### Task 6: Block detector

**Files:**
- Create: `src/adscout/sources/meta_web/blocks.py`
- Test: `tests/sources/meta_web/test_blocks.py`, `tests/fixtures/meta_web/blocks/*.txt`

**Interfaces:**
- Produces: `detect_block(url: str, inner_text: str) -> SourceBlocked | None`. Rules: URL path starts with `/login` or contains `/checkpoint/` → login/checkpoint; text contains (case-insensitive) `"you must log in"`, `"log in to continue"`, `"security check"`, `"confirm you're human"`, `"you're temporarily blocked"` or `"rate limit"` → the matching reason. `how_to_fix` copy: login → `"Meta asked for login. Run: uv run adscout browser — sign in once, then retry."`; checkpoint/captcha → `"Meta showed a security check. Open the browser with: uv run adscout browser, complete it yourself, wait 30+ minutes, then retry."`; temporary block → `"Meta rate-limited this browser. Wait a few hours before searching again."`

- [ ] **Step 1: Failing tests:** one per rule (synthetic text fixtures plus the real login-wall capture if Task 3 produced one), and `test_normal_results_page_not_blocked` using the real `inner_text.txt`.
- [ ] **Step 2–4:** FAIL → implement → PASS (pytest, ruff, mypy).
- [ ] **Step 5:** Commit `feat: detect Meta login walls and security checks`.

### Task 7: Database layer

**Files:**
- Create: `src/adscout/db/__init__.py`, `src/adscout/db/tables.py`, `src/adscout/db/repo.py`
- Test: `tests/db/test_repo.py`

**Interfaces:**
- Produces: tables `Snapshot` and `AdRow` per the spec's data model (`AdRow.media`, `platforms` and `raw` as JSON columns; `AdRow` has `snapshot_id` FK and a unique `(snapshot_id, ad_archive_id)`; datetimes are stored naive-UTC in SQLite and are re-attached to UTC on read through a `UTCDateTime` TypeDecorator). `make_engine(db_path: Path) -> Engine` (creates the parent dir, `create_all`); `create_snapshot(session, q: SearchQuery, source: str) -> Snapshot` (status `"running"`); `finish_snapshot(session, snapshot_id: int, result: SearchResult) -> Snapshot` (inserts ads, sets `status`, `ad_count`, `advertiser_count` (distinct page_name when page_id empty), `finished_at`, `error_message=result.message`); `fail_snapshot(session, snapshot_id, message: str)`; `list_snapshots(session, keyword: str | None = None) -> list[Snapshot]`; `load_ads(session, snapshot_id) -> list[Ad]`.

- [ ] **Step 1: Failing tests** with a tmp-path engine: `test_roundtrip_ads_preserve_utc` (start_date tzinfo is UTC after reload), `test_two_runs_create_two_snapshots`, `test_counts_set_on_finish`, `test_zero_ads_snapshot_ok`, `test_fail_snapshot_records_message`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: persist snapshots and ads in SQLite`.

### Task 8: MetaWebSource orchestration

**Files:**
- Create: `src/adscout/sources/meta_web/driver.py`, `src/adscout/sources/meta_web/source.py`
- Test: `tests/sources/meta_web/test_source.py`

**Interfaces:**
- Consumes: parsers (Tasks 4–5), `detect_block`, `build_library_url`, `Settings`.
- Produces:
  - `class PageDriver(Protocol)`: `async def open(self, url: str) -> None`, `async def scroll(self) -> None`, `def drain_payloads(self) -> list[str]` (captured response bodies + embedded JSON since the last call), `async def inner_text(self) -> str`, `async def current_url(self) -> str`, `async def close(self) -> None`. `PlaywrightDriver(profile_dir: Path, headless: bool)` implements it (`launch_persistent_context`, `page.on("response")` filtered to `/api/graphql` + `ad_archive_id`, scroll via `window.scrollTo(0, document.body.scrollHeight)`).
  - `MetaWebSource(settings: Settings, driver_factory: Callable[[], PageDriver] | None = None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, rng: random.Random | None = None)`, `name = "meta_web"`, `supports` → any 2-letter country. `search` loop: open → check block → loop { drain + parse → merge by id → progress → if ≥ limit: status `capped`, trim → if cancelled: `cancelled` → if 3 stalls: break → sleep(uniform(min, max)) → scroll → check block }. If no GraphQL ads were ever parsed, it falls back to `parse_inner_text(await inner_text())`. On a block it returns `SearchResult(status="blocked", ads=collected, message=f"{reason} {how_to_fix}")`. The driver is always closed (`finally`). A module-level `_SEARCH_LOCK = asyncio.Lock()` wraps `search`.

- [ ] **Step 1: Failing tests** with a `FakeDriver` fed scripted fixture payloads and an instant fake `sleep`: `test_collects_until_stall` (3 empty scrolls → stop, status ok), `test_respects_limit` (limit 5 → 5 ads, `capped`), `test_cancel_midway`, `test_block_midway_keeps_ads` (2nd scroll's inner_text is the login wall → `blocked`, ads from page 1 kept, message contains `uv run adscout browser`), `test_text_fallback_when_no_graphql`, `test_zero_results_ok`, `test_delays_within_bounds` (recorded sleep args all in [2, 5]), `test_driver_closed_on_error`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: add Playwright-backed MetaWebSource with polite scrolling`.

### Task 9: CLI `scrape`, `browser`, `snapshots`, and live verification

**Files:**
- Modify: `src/adscout/cli.py`
- Create: `src/adscout/services/scrape.py`
- Test: `tests/test_cli_scrape.py`

**Interfaces:**
- Produces: `async def run_scrape(q: SearchQuery, source: Source, engine: Engine, progress: ProgressCallback | None = None, cancel: CancelToken | None = None) -> Snapshot` (create → search → finish; on an unexpected exception, `fail_snapshot` and re-raise as `SourceError`). This is reused by the API in M3. CLI commands:
  - `adscout scrape KEYWORD [--country US] [--all] [--media all] [--limit 300] [--headless]`: a Rich live line `Found {n} ads · {a} advertisers · {phase}…`, then a summary table (snapshot id, ads, advertisers, oldest ad date + days, top 5 advertisers by count). Ctrl+C cancels gracefully. Exit code 0 for ok/capped/cancelled, 2 for blocked (prints the message in yellow).
  - `adscout browser`: opens a headful persistent profile at `https://www.facebook.com/ads/library/` and waits until the window is closed (for one-time login or a manual check).
  - `adscout snapshots [KEYWORD]`: a table of past runs.
- [ ] **Step 1: Failing tests** (CliRunner + a fake Source injected through `adscout.cli.get_source` monkeypatch + tmp DB): `test_scrape_saves_snapshot_and_prints_summary`, `test_scrape_zero_results_message` (`No ads found for "zzqxwv" in US. Try a broader keyword.`), `test_scrape_blocked_exit_code_2`, `test_snapshots_lists_runs`.
- [ ] **Step 2–4:** FAIL → implement → PASS (pytest, ruff, mypy).
- [ ] **Step 5: Live verification:** `uv run adscout scrape "posture corrector" --country US --limit 100`. Expected: ≥ 20 ads saved and a summary table. Then `uv run adscout snapshots`. Capture the output as milestone evidence.
- [ ] **Step 6:** Commit `feat: add scrape, browser and snapshots CLI commands`.

### Task 10: Optional MetaApiSource (EU/UK, official API)

**Files:**
- Create: `src/adscout/sources/meta_api.py`, `tests/fixtures/meta_api/ads_archive_page1.json` (synthetic, built from the documented field list)
- Test: `tests/sources/test_meta_api.py`

**Interfaces:**
- Produces: `MetaApiSource(token: SecretStr, client: httpx.AsyncClient | None = None)`, `name="meta_api"`, `supports(q)` → `q.country in EU_UK_CODES` (EU27 + `GB`). Calls `GET https://graph.facebook.com/v23.0/ads_archive` with `search_terms`, `ad_reached_countries=[CC]`, `ad_active_status=ACTIVE|ALL`, `ad_type=ALL`, `fields=id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,ad_creative_bodies,ad_creative_link_titles,ad_creative_link_captions,ad_snapshot_url,publisher_platforms,eu_total_reach,languages`, `limit=100`, following `paging.next` until the limit. Maps to `Ad` (`is_active = stop_time is None`, `media=[]` here because the API has no direct media URLs). A missing token means the source is not registered. A Graph error (`OAuthException`) → `SourceBlocked("Meta API token rejected.", "Create a new token at developers.facebook.com/tools/explorer and set META_ACCESS_TOKEN in .env.")`. CLI `get_source(q)` picks `meta_api` when the token is set and the country is EU/UK, and `meta_web` otherwise (`--source` overrides).
- [ ] **Step 1: Failing tests** (respx): `test_maps_fields`, `test_follows_paging_until_limit`, `test_token_error_is_blocked`, `test_supports_only_eu_uk`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: add optional official Ad Library API source`.

---

## Later milestones (detailed plan written just before each starts)

- **M2 — Scoring:** `scoring/relevance.py` (keyword-token filter + `Exclusion`s), `scoring/creatives.py` (collation/text+media dedupe), `scoring/factors.py` (the spec's thresholds and weights), `scoring/score.py` → `WinnerScore{total, provisional, factors[{key, value, display, threshold_pass, threshold_fail, status, points, max_points}], warnings}`, a CLI `adscout report <snapshot_id>`, and golden-fixture tests with hand-computed expected scores.
- **M3 — GUI v1:** FastAPI app (`adscout serve`; `adscout` with no args opens :8787), `POST /api/searches` + SSE `/api/searches/{id}/events`, report endpoints, media downloader (+ phash), and a Vite/React/TS/Tailwind/shadcn frontend: search bar, live progress, report (score breakdown, timeline histogram, gallery, advertiser leaderboard, exclusion toggles), dark/light themes, teaching empty states.
- **M4 — Claude analysis:** `Analyzer` protocol, CLI and API backends, a Pydantic result schema (angles, hooks, offers, verdict, suggested exclusions), batching + dedupe, cache table, settings UI, and an angles panel on the report.
- **M5 — Watchlist & co.:** WatchItem/Alert tables, an APScheduler daily job, trend charts, ±30% alerts (+ optional Windows toast via `win11toast`), a compare view (2–4), CSV export, and a PNG/PDF one-page report (Playwright `page.pdf`/screenshot of a print route).
- **M6 — Ship it public:** README (hero screenshot, 20 s GIF, Mermaid architecture, 3-command setup, disclaimer), MIT license, GitHub Actions (ruff, mypy, pytest, eslint, tsc, vitest), gitleaks, issue templates, and repo creation under SmitHunter (with the user's go-ahead before pushing).
