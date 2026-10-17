# M1 follow-ups (carried into later milestone plans)

Triage from the M1 whole-branch review. Each item names the milestone that should pick it up.

## M2 — Scoring
- `Ad.advertiser_key` property; replace the three copies of the page_id/page_name rule (`sources/base.py`, `db/repo.py`, `cli.py`). Empty id + empty name should not count as an advertiser.
- `parse_graphql`: an out-of-range `end_date` discards the whole ad; null the field instead.
- `parse_graphql`: HTML-vs-JSON detection heuristic; use `lstrip().startswith("<")`.
- Test `parse_payloads` with a mix of bad and good nodes.
- Text fallback parser: strip trailing CTA/link-card lines from `body` on non-video ads.
- `MetaApiSource._strip_token`: catch `httpx.InvalidURL` so one malformed item is skipped, not fatal.

## M3 — GUI v1
- Move `get_source` and the search lock into `services/`; create the lock per event loop; show a "queued" phase.
- `run_scrape`: expose the snapshot id before the search runs (needed for SSE).
- Cancel-aware sleep (cancel latency is up to ~6.5 s); Ctrl+C/cancel during `driver.open`.
- Shared `SnapshotStatus` Literal; type `fail_snapshot(status=...)`.
- Startup sweep that marks stale `running` snapshots as `error` (hard kill leaves them).

## M4 — Claude analysis / settings UI
- Validate `scroll_delay_min <= scroll_delay_max` and non-negative when settings become editable.

## M5 — Watchlist
- A `capped` snapshot's `ad_count` is a floor; trend alerts (±30%) must not treat it as exact.
- Watch DB growth from `raw` JSON under the daily scheduler.

## M6 — Ship it public
- Autouse conftest fixture isolating tests from ambient env vars.
- Bounds tests for keyword/limit; per-rule exact `how_to_fix` tests.
- gitleaks allowlist for CDN-hash false positives in fixtures ("LSD…", "EAAAA…").
- Verify the Bearer-header auth and `ad_snapshot_url` token stripping against the live Graph API before documenting the API source.
- Decide whether `BUILD-PROMPT.md` (now `docs/build-prompt.md`) stays public (contains a local path); document that `data/` is relative to the working directory.

# M2 follow-ups (from the M2 whole-branch review)

## M3 — GUI v1
- `build_report` returns one `Report` model (snapshot, score, timeline, top advertisers, ads) so the CLI and API don't reload ads.
- Show excluded ads with their reason in the report; add a `template` relevance reason (or warning) for DCO `{{product.*}}` ads that can't be keyword-checked.
- Settle keyword normalisation before overrides become common: stop words ("for", "women"), `casefold`, lens/lenses stemming; store the raw keyword alongside `keyword_norm` so keys can be rebuilt.
- Snapshot with status `error`/`running` and no ads: show "This snapshot has no saved ads ({status})." instead of the no-relevant-ads warning.
- `adscout exclude`: warn when no snapshot has that normalised keyword.
- `set_override`: catch `IntegrityError` and update (concurrent API writes).
- Perceptual-hash creative dedupe (media downloaded in M3); hash more than `media[0]`.
- Relevant ads with no advertiser key; Typer error console and stderr encoding; sparkline compaction (the GUI chart replaces it).
- `_leader` recomputes `group_creatives` per advertiser (perf nit).

## M5 — Watchlist / i18n
- Fold ß/ø/æ in `tokens` (together with any normalisation change, since it changes override keys).

## M6 — Ship it public
- mypy over `tests/` reports 9 errors (tests aren't under strict today); decide whether CI type-checks tests.

# M3 follow-ups (from the M3 whole-branch review)

## M4 — Angle analysis
- Validate the scroll-delay settings once they become editable in the Settings screen.

## M5 — Watchlist
- Cross-process lock in `data/`: CLI scrapes and GUI searches share one browser profile.
- Single-instance check before `sweep_running` (a second `adscout serve` on the same port marks the live snapshot interrupted).
- Give `SearchManager` a queue (not `SearchBusy`) for scheduled re-checks.
- Media dedupe across snapshots (content hash) so re-runs don't grow `data/media/`.
- Notice for an `error` snapshot that still has ads.
- Gallery filters persist across tabs.
- Gallery vs score creative grouping can differ slightly (greedy phash merge over all ads vs relevant ads).

## M6 — Ship it public
- README screenshots: use synthetic or blurred creatives; the local screenshots show real advertisers' ads.
- Replace the real brand in `web/src/features/report/fixtures.ts` with a made-up one and a `.test` domain.
- Media downloader: allow only `https://` (ideally `*.fbcdn.net`); cross-check Content-Type against `MediaItem.type`.
- Small hardening: unstarted SSE generator leak; non-GET on unknown `/api/*` returns 405; client shows a non-JSON error body raw; `api.get<T>` generics; `font-dense` token; sonner default styles; a11y pass (`<time>` Enter key, always-mounted aria-live region); Recharts lazy-load (main chunk 546 kB); `screenshots.py` localhost guard; only allow test Host names under tests.
- CI: Windows/macOS smoke (`npm ci && build && adscout serve` + GET); openapi drift check against `dump_openapi.py`; check the built wheel ships `static/`.

# M4 follow-ups (from the M4 whole-branch review)

## M5 — Watchlist (folded into the M5 plan)
- Reuse analyses across snapshots and cap automatic analyses per day; prune finished analysis jobs; run `AnalysisManager.start()` DB reads off the event loop; clamp effective scroll delays; keep phash grouping consistent between analysis and report.

## M6 — Ship it public
- Confirm dialog says "sends {n} ads" while n counts creatives — say "creatives".
- Origin check: allow only the served port and the Vite dev port (the analysis endpoint spends quota).
- Claude Code subprocess: isolate from the user's own settings/hooks if the CLI supports it (e.g. `--setting-sources`).
- Loose not-logged-in heuristic; README must say "cached per snapshot" (plus cross-snapshot reuse once M5 lands) and that `auto` prefers an API key when one is set.
- Screenshots contain real brands, advertisers and ad copy (real brands): use a synthetic demo snapshot or blur before committing.
- mypy over `tests/` (32 errors); fieldFor fallback and confirm-dialog Esc/focus tests; relative data_dir in get_source tests.

# M5 follow-ups (from the M5 whole-branch review)

## M6 — Ship it public
- Media blob `.part` names via mkstemp (cross-process); cancel-during-open can outlive close.
- `/api/watch` and `/trend` rebuild reports per request — cache per (snapshot, analysis).
- Pin allowed Origins to the bound port + Vite dev port; disable Remove while an item is running; show advertiser watch scope (ALL); server-side alerts limit.
- PDF fit check with long names / many advertisers (or drop `page_ranges`).
- `watch run ID` on a busy profile should print the busy copy; manual click joining a capped auto job.
- Measure a warm watch run; if still slow, download media for relevant leads only.
- CI: `playwright install --with-deps chromium` on Linux (or env-skip export tests), `PYTHONUTF8=1` on Windows.
- README: local-only (127.0.0.1), daily check once/day with < 24 h catch-up and stop-on-block, auto-analyze off + daily cap 5, `notify` extra, serve logs to stderr.
- `docs/screenshots/` (untracked) shows real ads/brands — regenerate from demo mode; never commit the old ones.
