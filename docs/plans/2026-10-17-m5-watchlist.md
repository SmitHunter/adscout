# AdScout M5 — Watchlist, Scheduler, Trends, Compare, Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. UI tasks load `frontend-design:frontend-design` first.

**Goal:** Users can watch products (keywords) and advertisers. AdScout re-checks them daily at a chosen time and charts the trends: active ads, new creatives, advertisers, and Winner Score. It alerts when active ads move ±30%. Users can also compare 2–4 snapshots side by side, and export a snapshot as an ads CSV or a one-page PDF/PNG report.

**Architecture:**
- `adscout.watch` package: pure trend and alert rules plus a runner. An APScheduler `AsyncIOScheduler` runs inside `adscout serve` and drives the runner.
- `SearchManager` gains a queue so scheduled runs wait their turn, one browser at a time.
- Cross-process file locks keep the CLI and the server from fighting over the browser profile. A single-instance lock prevents two servers.
- Exports render a print-only React route to PDF/PNG with the Playwright that is already installed.
- Media is de-duplicated across snapshots by URL path and content hash, so daily re-checks don't grow `data/media/` endlessly.

**Tech Stack:** APScheduler 3.x, filelock, Playwright (existing), FastAPI, React (existing stack), and optionally `desktop-notifier` as an extra.

**Spec:** `docs/build-prompt.md` § "4. Watchlist", "5. Compare", "6. Export". Follow-ups: `docs/plans/2026-10-17-m1-followups.md`, sections tagged M5 (from M1, M2 and M3).

## Global Constraints

- Python 3.12, mypy strict, ruff check + format clean, pytest offline (no Meta, no Claude, and no real browser except the Playwright export test, which renders a local static page). Web: TS strict, ESLint, Prettier, Vitest, build. Commits end with a blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Politeness (non-negotiable):**
  - Scheduled checks run one watch item at a time, with a randomised 30–90 s gap between items.
  - A day's run never starts a second time.
  - A blocked result stops the rest of that day's run and raises an alert `Meta blocked the daily check. Open the app to sign in.`
  - Nothing is scheduled unless the user has enabled watch items.
  - A missed run (the app was closed at the scheduled time) runs once at the next startup if it was missed by less than 24 h. Older misses are skipped.
- **Daily time:** setting `daily_check_time` "HH:MM" in the display timezone, default `"09:00"`. Editable in Settings.
- **Watch kinds:**
  - `keyword`: query, country, active_status, media_type.
  - `advertiser`: page_id + name, plus country (default `ALL`).
  - Advertiser searches use the Ad Library page view: `view_all_page_id={page_id}`, `search_type=page`, `country=ALL|CC`. Every ad in an advertiser snapshot is relevant; the keyword filter doesn't apply.
- **Alert rule:**
  - Compare the relevant active-ad count of the new snapshot with the previous snapshot of the same watch item. Alert when |Δ| ≥ 30% and the previous count ≥ 5.
  - Capped snapshots are floors. If both are capped, skip. If only the new one is capped, only an increase may alert, worded `≥ +N%`. If only the previous one is capped, only a decrease may alert.
  - Message: `{query}: active ads {up|down} {pct}% ({prev} → {now})`.
  - A blocked run raises the block alert above instead.
- **Trend series** per watch item, over its ok/capped snapshots ordered by time:
  - `date` (local)
  - `active_ads` (relevant active), with a `capped` flag
  - `new_creatives`: creative keys not present in any earlier snapshot of the item; the first snapshot counts all
  - `advertisers`
  - `winner_score` (headline)
  - `market_score`
- **Copy (exact):**
  - Watch button: `Watch`. Watching: `Watching`.
  - Empty watchlist: `Watch a product or advertiser to track it daily.`
  - Run-now toast: `Checking {query} now.`
  - Single instance: `AdScout is already running at http://127.0.0.1:{port}` (then open that URL and exit 0).
  - Profile lock: `Another AdScout search is using the browser. Wait for it to finish, then try again.`
- **Exports:**
  - CSV: one row per ad, UTF-8 with BOM for Excel. Columns: `ad_archive_id, advertiser, page_id, start_date, end_date, days_running, is_active, relevant, reason, creative_key, collation_count, title, body, link_url, landing_domain, media_types`. Dates are ISO in the local timezone.
  - PDF: A4 portrait, one page. PNG: 1600 px wide.
  - Both render the `/report/:id/print` route: score block, factor rulers, verdict (if analysed), monthly timeline, top 6 creatives, top 5 advertisers. Third-party creative thumbnails appear as in the app; the export is the user's own data.
- **Desktop notifications:** optional extra `notify` (`desktop-notifier`). Setting `desktop_notifications` default false. If the package is missing, the toggle is disabled with tooltip `Install with: uv sync --extra notify`.
- **Claude cost safety for unattended runs (M4 review):**
  - **Cross-snapshot reuse:** before calling Claude for any snapshot (manual, auto-analyze or watch run), look for the most recent stored analysis of the same keyword or page and country with the same `prompt_version` and model. Reuse it when its lead creative set covers ≥ 90% of the new snapshot's relevant lead creatives (compare by `creative_key`, and remap ids to the new snapshot's leads). Store the reused result as a new `Analysis` row with `backend="reused"` and `usage=null`. `force=True` skips reuse.
  - **Daily cap:** setting `max_auto_analyses_per_day` (default 5) counts auto-analyze and watch-run analyses only. Over the cap, skip and log; manual runs are never capped.
  - **Hygiene:** prune finished jobs from `AnalysisManager._jobs` older than 1 h. `start()` does its DB reads via `asyncio.to_thread`. `effective_settings()` clamps scroll delays so min ≤ max even when `.env` and the saved file disagree.

## Review Focus

1. **App closed at the scheduled time:** the catch-up runs once on the next start (< 24 h), never twice. A second server instance must not run the schedule. Tests in Tasks 1 and 3.
2. **CLI `scrape` while the server is running a scheduled check:** the CLI waits briefly, or exits with the profile-lock copy. The browser profile is never opened twice. Test in Task 1.
3. **Capped snapshots and alert noise:** alerts follow the floor rule, and no alert fires from a 300-ad cap jumping to 300. Tests in Task 2.
4. **Deleting a watch item:** its snapshots stay (history), its alerts are removed, and its schedule entry is gone. Tests in Tasks 3 and 4.
5. **Export of a snapshot with missing media, or with no analysis:** the PDF still renders with placeholders, and the verdict section is omitted. Tests in Tasks 4 and 6.

---

### Task 1: Infra — queue, single instance, profile lock, media dedupe, notices

**Files:**
- Modify: `src/adscout/services/searches.py`, `src/adscout/api/app.py`, `src/adscout/cli.py`, `src/adscout/sources/meta_web/driver.py`, `src/adscout/services/media.py`, `src/adscout/api/routes.py` (media route), `src/adscout/services/report.py`, `pyproject.toml` (add `filelock`)
- Create: `src/adscout/locks.py`
- Test: `tests/services/test_search_queue.py`, `tests/test_locks.py`, `tests/services/test_media_dedupe.py`, `tests/services/test_report.py`

**Interfaces:**
- **`locks.py`:**
  - `server_lock(data_dir) -> FileLock`, non-blocking. `adscout serve` acquires it before `sweep_running`. If it is held, print the single-instance copy, open the URL, and exit 0. Store the port in `data/server.port`.
  - `profile_lock(data_dir, timeout_s: float = 10) -> FileLock`. Every browser launch (scrape, the `browser` command, `/api/browser/open`, PDF export) holds it for the browser's lifetime. On timeout, raise `ProfileBusy` (a `SourceError` subclass) with the profile-lock copy.
- **SearchManager:**
  - `enqueue(q, origin: Literal["manual", "scheduled"]) -> int` (snapshot id). Scheduled requests queue FIFO behind a running search.
  - `start()` (manual) still raises `SearchBusy` while anything runs or is queued. The 409 copy stays.
  - `queue_size() -> int`.
  - `active()` includes `queued: int`.
- **Media dedupe:**
  - Before downloading, look up `data/media/index.json` (normalised URL path → blob name).
  - After downloading, hash the content (sha256[:32]) into `data/media/blobs/<hash>.<ext>`.
  - `local_path`/`poster_path` become `blobs/<file>`.
  - Add the route `GET /media/blobs/{filename}` with the same validation. Old per-snapshot paths keep working.
- **Report notice:** an `error` snapshot with ads > 0 → `This search stopped with an error: {error_message}` (warning tone).
- **Gallery filters** persist across tab switches (lift the state into `ReportTabs`), so the web is touched too.
- [ ] **Step 1: Failing tests:**
  - `test_scheduled_enqueue_waits_for_running_search`
  - `test_manual_start_busy_while_queue_non_empty`
  - `test_server_lock_second_instance_exits_with_copy` (CliRunner, lock pre-held)
  - `test_profile_lock_timeout_message`
  - `test_same_url_path_downloads_once_across_snapshots`
  - `test_identical_content_different_paths_one_blob`
  - `test_blob_route_validates_filename`
  - `test_error_snapshot_with_ads_notice`
  - web `filters persist across tabs`
- [ ] **Step 2–4:** FAIL → implement → PASS (Python + web checks).
- [ ] **Step 5:** Commit `feat: search queue, single-instance and profile locks, media dedupe`.

### Task 2: Advertiser searches, watch/alert tables, trend + alert rules

**Files:**
- Create: `src/adscout/watch/__init__.py`, `src/adscout/watch/rules.py`
- Modify: `src/adscout/models/ad.py` (SearchQuery `kind`, `page_id`), `src/adscout/sources/meta_web/urls.py`, `src/adscout/db/tables.py`, `src/adscout/db/repo.py`, `src/adscout/services/report.py`, `src/adscout/scoring/score.py` (relevance bypass for advertiser kind)
- Test: `tests/watch/test_rules.py`, `tests/db/test_watch_repo.py`, `tests/sources/test_base.py`, `tests/services/test_report.py`

**Interfaces:**
- **`SearchQuery`:**
  - `kind: Literal["keyword", "advertiser"] = "keyword"`.
  - `page_id: str | None`, required when `kind == "advertiser"`, digits only.
  - `keyword` holds the advertiser display name for advertiser kind.
  - `country` also accepts `"ALL"` for advertiser kind only.
  - `build_library_url` emits the page-view params for the advertiser kind.
  - `Snapshot.kind` and `Snapshot.page_id` (new nullable column, added via `_add_missing_columns`) are stored.
- **Relevance:** `score_ads(..., kind="advertiser")` marks every ad relevant with reason `"keyword"`.
- **Tables:**
  - `WatchItem`: id, kind, query, page_id, country, active_status, media_type, enabled, created_at, last_run_at, last_status.
  - `Snapshot.watch_item_id` (nullable FK).
  - `Alert`: id, watch_item_id, snapshot_id, kind (`change|blocked`), change_pct, message, created_at, read.
- **Repo:** CRUD for watch items (delete removes the item's alerts and nulls `Snapshot.watch_item_id`), `snapshots_for_watch(item_id)`, `add_alert`, `list_alerts(unread_only)`, `mark_read(ids|all)`.
- **`rules.py` (pure):**
  - `TrendPoint`
  - `trend(points_input: list[SnapshotSummary]) -> list[TrendPoint]`
  - `alert_for(prev: SnapshotSummary | None, now: SnapshotSummary, query: str) -> AlertDraft | None`
  - `SnapshotSummary`: id, started_at, status, active_relevant, creative_keys: set[str], advertisers, winner, market, capped.
- [ ] **Step 1: Failing tests:**
  - `test_advertiser_url_page_view_params`
  - `test_advertiser_kind_all_ads_relevant`
  - `test_trend_new_creatives_counts_unseen_keys`
  - `test_alert_up_30` / `test_alert_down_30`
  - `test_no_alert_prev_below_5`
  - `test_capped_floor_rules` (all four combinations)
  - `test_message_copy`
  - `test_delete_watch_keeps_snapshots_removes_alerts`
  - `test_alerts_mark_read`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: advertiser searches, watchlist tables and trend/alert rules`.

### Task 3: Scheduler + watch runner + notifications + CLI

**Files:**
- Create: `src/adscout/watch/runner.py`, `src/adscout/watch/scheduler.py`, `src/adscout/watch/notify.py`
- Modify: `src/adscout/api/app.py` (lifespan), `src/adscout/cli.py`, `src/adscout/settings_store.py` (`daily_check_time`, `desktop_notifications`), `pyproject.toml` (`apscheduler>=3.10,<4`, optional extra `notify = ["desktop-notifier"]`)
- Test: `tests/watch/test_runner.py`, `tests/watch/test_scheduler.py`, `tests/test_cli_watch.py`

**Interfaces:**
- **`WatchRunner(engine, manager: SearchManager, sleep, rng, notifier)`:**
  - `async run_all(now) -> RunSummary` runs the enabled items sequentially via `manager.enqueue(..., "scheduled")`, awaiting each terminal event (use `subscribe`).
  - Between items it sleeps `uniform(30, 90)`.
  - On blocked: add the blocked alert and stop.
  - After each item: build the summaries, call `alert_for`, store the alert, notify if enabled.
  - Updates `last_run_at`/`last_status`.
  - Records the day's run in `data/watch_runs.json` (`{date: "YYYY-MM-DD", started_at, finished_at}`) so it never runs twice a day.
  - `async run_one(item_id)` is "Run now" and has no gap.
- **`WatchScheduler(runner, settings_provider, clock)`:**
  - `start()` registers a cron job at `daily_check_time` in tz (APScheduler `CronTrigger`, `coalesce=True`, `max_instances=1`, `misfire_grace_time=3600`).
  - On startup, checks for a missed run (< 24 h, none recorded today) and schedules an immediate catch-up.
  - `reschedule()` runs when settings change.
  - `shutdown()`.
  - The scheduler only starts in `serve` after `server_lock` is acquired.
- **`notify.py`:**
  - `async notify(title, body)` uses `desktop-notifier` if it is importable and enabled. Otherwise it is a no-op.
  - `available() -> bool`.
- **CLI:**
  - `adscout watch add KEYWORD [--country] | --advertiser PAGE_ID --name NAME`
  - `adscout watch list` (table: id, kind, query, country, enabled, last run, last status)
  - `adscout watch run [ID]` (runs through the CLI's own SearchManager, holding the profile lock)
  - `adscout watch remove ID`
  - `adscout watch enable|disable ID`
- **Analysis reuse and cap:** the global "Claude cost safety" constraints live in `src/adscout/services/analysis.py` (`find_reusable_analysis`, cap counter in `data/analysis_runs.json`). The watch runner triggers analysis through `AnalysisManager.start_after_search` only when `auto_analyze` is on.
- [ ] **Step 1: Failing tests** (fake SearchManager/source, fake clock, instant sleep):
  - `test_reuses_previous_analysis_when_creatives_overlap_90pct` (FakeAnalyzer 0 calls; new row `backend="reused"`)
  - `test_no_reuse_below_overlap_or_force`
  - `test_daily_auto_cap_skips_and_manual_not_capped`
  - `test_jobs_pruned_after_an_hour`
  - `test_effective_scroll_delays_clamped`
  - `test_run_all_sequential_with_gaps_in_range`
  - `test_blocked_stops_and_alerts`
  - `test_alert_created_and_notified`
  - `test_never_twice_same_day`
  - `test_catch_up_on_startup_within_24h` / `test_no_catch_up_after_24h`
  - `test_reschedule_on_time_change`
  - `test_scheduler_not_started_without_server_lock`
  - `test_cli_watch_add_list_remove`
  - `test_notify_noop_without_package`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: daily watchlist scheduler with alerts`.

### Task 4: API — watchlist, trends, alerts, compare, exports

**Files:**
- Create: `src/adscout/services/export.py`
- Modify: `src/adscout/api/routes.py`, `src/adscout/api/deps.py`, `web/openapi.json`
- Test: `tests/api/test_watch_routes.py`, `tests/api/test_compare_export_routes.py`, `tests/services/test_export.py`

**Interfaces (stable operation ids):**
- **Watchlist:**
  - `GET /api/watch` → items with `latest` (snapshot summary), `sparkline` (last 14 active_ads), `next_run_at`, `unread_alerts`.
  - `POST /api/watch` (keyword or advertiser body; duplicate → 409 `Already watching this.`).
  - `PATCH /api/watch/{id}` (`enabled`).
  - `DELETE /api/watch/{id}`.
  - `POST /api/watch/{id}/run` → 202, plus the run-now toast copy as `message`.
  - `GET /api/watch/{id}/trend` → `list[TrendPoint]` + item.
- **Alerts:** `GET /api/alerts?unread=true`; `POST /api/alerts/read` (`{ids}` or `{all: true}`).
- **Compare:** `GET /api/compare?ids=1,2` (2–4, else 422) → `list[CompareColumn]`: snapshot, score, leader, factors (leader + market), timeline_month, analysis verdict (or null).
- **Exports:**
  - `GET /api/snapshots/{id}/export.csv` → `text/csv` attachment `adscout-{slug}-{id}.csv`.
  - `GET /api/snapshots/{id}/export.pdf` and `.png` → rendered by `services/export.py`: `render_report(base_url, snapshot_id, fmt) -> bytes` launches headless Chromium (Playwright, holding the profile lock? No: use a fresh non-persistent browser, so no profile lock), opens `{base}/report/{id}/print`, waits for `[data-print-ready]`, and calls `page.pdf(format="A4", print_background=True)` or `screenshot(full_page=True)` at width 1600. 30 s timeout → 504 `Export timed out.`.
- **Settings** gain `daily_check_time` (validated `HH:MM`), `desktop_notifications`, `notifications_available`. PUT reschedules.
- [ ] **Step 1: Failing tests:**
  - CRUD + 409 + run-now (fake runner)
  - trend shape
  - alerts read
  - compare 422 on 1 or 5 ids
  - compare columns
  - CSV header, BOM and rows (relevant and excluded with reason)
  - CSV escapes formulas: cells starting with `= + - @` get a leading `'` to prevent CSV injection
  - export PDF/PNG via a fake renderer
  - `test_render_report_real_playwright` renders a static HTML file with `data-print-ready` via `file://` and asserts the PDF magic bytes `%PDF`; skip if Chromium isn't installed
  - cross-origin 403 on POST/PATCH/DELETE
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: watchlist, alerts, compare and export endpoints`.

### Task 5: Frontend — watchlist, trends, alerts

**Files:**
- Create: `web/src/features/watch/{WatchlistPage.tsx,WatchItemPage.tsx,TrendCharts.tsx,WatchButton.tsx,AlertsMenu.tsx}` + tests
- Modify: `web/src/app/{routes.tsx,Shell.tsx}`, report header and advertiser table (Watch buttons), `web/src/features/settings/SettingsPage.tsx` (daily time, notifications)

**Behaviour:**
- **Rail:** `Watchlist` link with an unread-alert count badge, plus an alerts menu (a popover listing alerts, `Mark all read`).
- **Watchlist page:**
  - Table: query (kind icon via text label `Product`/`Advertiser`), country, a 14-point active-ads sparkline, latest Winner Score (chip colour), last run (relative), status, enabled toggle, `Run now`, `Remove` (confirm).
  - The next scheduled run is shown in the header: `Next check {weekday} {HH:MM}`.
  - Add form: product keyword + country.
  - Empty state copy.
- **Watch item page (`/watch/:id`):**
  - Four small-multiple charts sharing one x-axis: active ads (capped points marked as a hollow dot with tooltip `At the search limit`), new creatives per check, advertisers, Winner Score.
  - The alerts list for the item.
  - Links to each snapshot's report.
- **Report header:** `Watch`/`Watching` button. **Advertiser table:** per-row `Watch`, which creates an advertiser watch.
- **Settings:** daily check time (time input), desktop notifications toggle (disabled with the install tooltip when unavailable).
- [ ] **Step 1: Failing tests:**
  - `watchlist renders sparkline and next run`
  - `add watch posts and lists`
  - `remove confirms`
  - `run now toasts copy`
  - `trend charts mark capped points`
  - `alerts badge and mark all read`
  - `watch button toggles on report`
  - `advertiser watch from table`
  - `settings daily time validation`
- [ ] **Step 2–4:** FAIL → implement → PASS (full web checks).
- [ ] **Step 5:** Commit `feat: watchlist, trends and alerts UI`.

### Task 6: Frontend — compare, export, print route

**Files:**
- Create: `web/src/features/compare/ComparePage.tsx`, `web/src/features/report/{ExportMenu.tsx,PrintReport.tsx}` + tests
- Modify: routes, Shell (rail link `Compare`), `ReportPage.tsx` (enable Export)

**Behaviour:**
- **Compare (`/compare?ids=`):**
  - Picker: a multi-select of snapshots (keyword, country, date), up to 4.
  - Columns side by side, each with: Winner Score (headline + leader), market demand, factor rulers aligned row by row (same factor, same row), mini monthly timeline, verdict (if any).
  - The best value per factor row is highlighted with a `--signal` outline.
  - Fully usable at 1366 with 4 columns.
- **Export menu** on the report header: `Download CSV`, `Download PDF`, `Download PNG`. Uses links to the export endpoints with a pending state, and an error toast on failure.
- **Print route (`/report/:id/print`):**
  - Light theme forced, A4-proportioned layout, no rail, no motion.
  - Sets `data-print-ready` on `<body>` once the report and images have loaded (images: wait for load/error, max 10 s).
  - Contents: score, rulers, verdict, timeline, top 6 creatives grid, top 5 advertisers. A footer with snapshot info and `Generated by AdScout`.
- [ ] **Step 1: Failing tests:**
  - `compare aligns factor rows and highlights best`
  - `compare picker limits to four`
  - `export menu links`
  - `print route sets ready after images`
  - `print omits verdict without analysis`
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat: compare view, export menu and printable report`.

### Task 7: Live check + QA

- [ ] **Step 1:**
  - Build the web app and serve it.
  - Add a watch for `posture corrector` US through the GUI, then `Run now` once. This is one real, polite scrape.
  - Confirm a new snapshot is linked to the item and the trend shows 2 points (the existing snapshot 1 is linked by a one-off repo call, or the trend shows 1 point; document which).
  - **Disable the watch item afterwards**, so no background scraping happens without the user opting in.
- [ ] **Step 2:**
  - Export snapshot 1 as CSV, PDF and PNG via the API. Open the PDF page count with pypdf (dev dep), expected 1.
  - Compare snapshots 1 and 2.
  - Screenshot: watchlist, watch item trends, compare, print route, report with the export menu. Settings at 1366 dark + light.
  - Critique, fix, and re-shoot.
- [ ] **Step 3:** Run the full checks. Commit `chore: M5 QA fixes`. Leave `docs/screenshots/` untracked.

---

## Rulings in this plan (made by Claude; cheap to change)

1. The scheduler lives inside `adscout serve`, not an OS service. It only runs while the app is open, and missed runs are caught up within 24 h. This keeps it local-first and permission-free on all three OSes.
2. Alerts track ±30% in relevant active ads only. Capped snapshots are treated as floors, so caps don't cause noise.
3. Advertiser watches use the Ad Library page view, and every ad in an advertiser snapshot counts as relevant.
4. PDF/PNG export renders the app's own print route with headless Playwright, so there is no second rendering stack.
5. CSV cells starting with `= + - @` are prefixed with `'` to block spreadsheet formula injection from ad copy.
6. The live test's watch item is left disabled, so AdScout never scrapes in the background unless the user turns watching on.
