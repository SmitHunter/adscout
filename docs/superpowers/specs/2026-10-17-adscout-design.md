# AdScout — Design

Date: 2026-10-17 · Source brief: `docs/build-prompt.md` (authoritative for anything not restated here).

## Intent

Automate the manual "is this dropshipping product a proven winner?" check done today by scrolling the Meta Ad Library. The bar (from the brief): ads running 3–6+ months, 10+ distinct creatives, 3–5 distinct emotional angles, evergreen demand. Track products over time to spot advertisers who are scaling.

It ships as a public portfolio repo (github.com/SmitHunter/adscout), so code quality, README and screenshots matter as much as features.

## Goals / non-goals / anti-goals

- **Goals:** milestones M1–M6 from the brief, each runnable on its own.
- **Non-goals:** TikTok, Google Ads Transparency, Shopify revenue estimates, multi-user, cloud hosting, payments.
- **Anti-goals:** bypassing CAPTCHAs or login walls, high-volume scraping, a black-box score, committed secrets, a templated-looking UI.

## Decisions made by Claude (user said "just start")

1. **Noise handling:** a relevance filter plus manual exclusion. An ad counts toward the score only if the keyword (all tokens, case-insensitive, simple plural stemming) appears in its body, title, link caption or landing URL. The user can untick or re-tick any ad or advertiser in the report, and exclusions are stored per keyword so re-runs keep them. In M4, Claude can also flag "not selling this product" ads, which become suggested exclusions.
2. **Stack:** as written in the brief. Python 3.12 is pinned via `uv python install 3.12` (the machine has 3.13).
3. **Score weights:** Longevity 30, Creative volume 25, Angle diversity 25, Continuity 20. Saturation is shown as a warning only. Until angle analysis exists, the Angle factor shows "pending" and the score is normalised over the other factors, with a visible "provisional" badge. The headline Winner Score is the strongest advertiser's score (most creatives) on its own; the pooled score across all advertisers is shown as 'Market demand'.
4. **Factor scoring:** each factor maps its value to 0–1 with a linear ramp between a "fail" and a "pass" threshold. Thresholds live in one config module and are shown in the UI.
   - Longevity: the oldest active ad's age (fail < 30 d, pass ≥ 90 d, full ≥ 180 d), blended 50/50 with the share of ads older than 90 d.
   - Continuity: of the last 6 calendar months, the fraction that had ≥ 1 new creative start (pass ≥ 5/6, fail ≤ 2/6).
   - Volume: distinct creatives (fail < 3, pass ≥ 10, full ≥ 20).
   - Angles: distinct angle clusters (fail < 2, pass ≥ 3, full ≥ 5).
   - Saturation: distinct advertisers. 1–2 shows "unproven/early", 3–15 "healthy", > 15 "crowded".
   - Chip colours: green ≥ pass, amber between thresholds, red ≤ fail.
5. **Creative dedupe:** group by Meta's `collation_id` when present. Otherwise group by normalised body text plus the media URL path. From M3, media is downloaded and a perceptual hash (`imagehash.phash`, Hamming distance ≤ 6) merges near-duplicates. "N ads use this creative" is stored as `collation_count`.
6. **Evergreen:** not a scored factor in v1. The timeline chart shows it, and the Claude verdict comments on seasonality. Google Trends was left out to avoid another scraped source.
7. **Package layout:** a `src/adscout/` backend with subpackages `sources/` (Source protocol, `meta_web`, `meta_api`), `models/`, `db/`, `scoring/`, `analysis/` (Claude backends), `scheduler/`, `api/` (FastAPI routes + SSE), `cli.py`. The frontend lives in `web/` and builds to `src/adscout/static/`, which FastAPI serves.
8. **Scraper strategy:** first a recon spike on the live page to capture real GraphQL responses (the `ad_library_main` / `search_results_connection` payloads) and save sanitised samples to `tests/fixtures/`. The GraphQL parser is primary. An `innerText` parser is the fallback, auto-used when no GraphQL payloads are seen. A detector for login walls and checkpoints raises `SourceBlocked(reason, how_to_fix)`.
9. **Politeness defaults:** 2–5 s randomised delay between scrolls, a cap of 300 ads, a global lock (one search at a time), and a stop after 3 scrolls that add nothing new.
10. **Time:** stored as UTC `datetime`. Ad start dates from Meta are dates without a timezone and are stored as UTC midnight. The UI shows Australia/Melbourne (configurable).
11. **Claude backends:** an `Analyzer` protocol with `ClaudeCliAnalyzer` (`claude -p --output-format json`, schema enforced via prompt + Pydantic validation, 1 retry) and `AnthropicApiAnalyzer` (official SDK, structured outputs). Results are cached by `(snapshot_id, prompt_version, model)`.
12. **Config:** `pydantic-settings` reads `.env` + `data/settings.json` (UI-editable settings).

## Data model (SQLModel)

- `Snapshot`: id, query (keyword or advertiser page_id), kind, country, active_status, media_type, source, started_at, finished_at, status (running/ok/blocked/error/cancelled/capped) (ad_count is a floor when capped; M5 trend alerts must not treat it as exact), error_message, ad_count, advertiser_count.
- `Ad`: id, snapshot_id, source, ad_archive_id, page_id, page_name, start_date, end_date, is_active, body, title, link_url, link_caption, cta_text, display_format, media (JSON list of {type, url, local_path, phash}), collation_id, collation_count, platforms (JSON), raw (JSON, for debugging), relevant (bool, computed), creative_key.
- `Exclusion`: keyword, scope (ad/advertiser), target_id.
- `Analysis`: snapshot_id, model, backend, prompt_version, result JSON, created_at.
- `WatchItem`: id, kind (keyword/advertiser), query, country, enabled, created_at.
- `Alert`: id, watch_item_id, snapshot_id, change_pct, message, created_at, read.
- Settings: daily check time, timezone, Claude backend + model, result cap.

## Interfaces

```python
class Source(Protocol):
    name: str

    def supports(self, q: SearchQuery) -> bool: ...
    async def search(
        self,
        q: SearchQuery,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> SearchResult: ...  # ads + status (ok/blocked/cancelled/capped)
```

## Milestones

Same as the brief: M1 scraper core + CLI; M2 scoring; M3 GUI v1; M4 Claude analysis; M5 watchlist, scheduler, trends, compare, export; M6 ship public. Export (CSV + PNG/PDF) goes in M5, alongside compare.

## Left to the builder

Visual design details (guided by the frontend-design skill and the "trading terminal" direction), exact API route shapes, and chart library details (Recharts unless it blocks something).
