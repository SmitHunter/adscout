# Build prompt: AdScout (working name)

Paste everything below the line into a fresh Claude Code session started in the project folder.

---

You are building **AdScout**, a local desktop-browser app that tells me whether a dropshipping product is a proven winner, using public data from the **Meta Ad Library** (facebook.com/ads/library). It will become a **public GitHub repo** (SmitHunter/adscout), so code quality, README and screenshots matter as much as features.

## How to work

1. Start with the `superpowers:brainstorming` skill. Ask me questions **one at a time**, multiple choice where possible. Do not write code until I approve the design.
2. Then `superpowers:writing-plans` → a plan in `docs/plans/`, split into the milestones below.
3. Execute with `superpowers:subagent-driven-development` and `superpowers:test-driven-development`. One milestone at a time; show me the working result at the end of each before starting the next.
4. Load `frontend-design:frontend-design` (or `design-taste-frontend`) before any UI work. Load `claude-api` before writing any Claude integration code.
5. Commit after every task with clear messages. Never commit secrets: `.env` is gitignored, ship `.env.example`.

## The problem it solves

I validate products by hand today: search a keyword in the Ad Library, scroll, and eyeball how long ads have run and how many creatives exist. My validation bar for a "winner":

- **Longevity:** ads running continuously for **3–6+ months** (advertisers don't pay for losing ads that long)
- **Volume:** **10+ distinct creatives**
- **Angles:** **3–5 distinct emotional angles** in the ad copy (pain relief, confidence, gift, etc.)
- **Evergreen:** demand isn't a one-season fad

AdScout automates that check and tracks products over time.

## Data sources (important constraints)

1. **Ad Library web UI via Playwright (primary).** The official API only returns *commercial* ads that were delivered in the **EU/UK**. US/AU commercial ads are only available through the public web UI. Approach that already worked manually:
   - URL: `https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q=<keyword>&search_type=keyword_unordered&media_type=all`
   - **Preferred:** intercept the page's own GraphQL/XHR JSON responses with Playwright `page.on("response")`, which is far more robust than DOM selectors. Investigate the real response shape first and save samples as test fixtures.
   - **Fallback:** parse `document.body.innerText`, splitting on `"Library ID:"`. Each card contains `Started running on <date>`, the advertiser name (line before `Sponsored`), and `N ads use this creative`.
   - Load more results by scrolling (`window.scrollTo`), not clicking.
   - Use a **persistent browser profile** (`data/browser-profile/`) so I can optionally log in once. Make logged-out mode work if Meta allows it.
   - Be polite: low volume, randomised delays, a hard cap on results per search (default 300), and one search at a time. **Never** bypass CAPTCHAs or login walls. If blocked, stop and tell me in the UI.
2. **Official Ad Library API (optional, EU/UK only).** If `META_ACCESS_TOKEN` is set, offer EU/UK searches through `ads_archive` for richer fields (delivery start/stop dates, reach ranges). Design a `Source` interface so both sources return the same normalised `Ad` model.

## Core features

### 1. Search
- Keyword, country (default US; quick toggle AU / UK / CA / EU), active/all, media type.
- Live progress while scraping ("Found 142 ads · 38 advertisers · scrolling…"), cancellable.
- Every search is saved as a **snapshot** in SQLite, so re-running builds history.

### 2. Product report (the main screen)
- **Winner Score 0–100** with a visible breakdown. No black box; each factor shows its value, its threshold, and pass/fail against my bar above:
  - Longevity: oldest still-active ad's age, plus the share of ads older than 90 / 180 days
  - Continuity: are new creatives launched every month, or did everything start in one burst? (gaps = weak)
  - Creative volume: distinct creatives (dedupe using "N ads use this creative" and media hashes)
  - Angle diversity: number of distinct emotional angles (see Claude analysis)
  - Saturation: number of distinct advertisers (many = proven but crowded; show it as a warning, not a penalty)
- **Timeline chart:** histogram of ad start dates by week/month. This is the money chart.
- **Creative gallery:** thumbnails/videos, copy text, start date, days running, advertiser. Filter and sort by longest running. **Download media locally** (Meta's CDN URLs expire).
- **Advertiser leaderboard:** each advertiser's ad count, oldest ad and landing page domain; click through to their store.

### 3. Claude angle analysis
- Send the ad copy (deduped, batched) to Claude. It returns, as structured JSON: angle clusters (name, description, example ads, count), the hooks used, the offers seen (discounts, bundles, guarantees), and a 3-sentence verdict.
- Two backends behind one interface: **Claude Code CLI** in headless mode (`claude -p … --output-format json`, uses my subscription), or the **Anthropic API** with `ANTHROPIC_API_KEY`. Default model `claude-sonnet-5-5`, configurable in settings.
- Cache results per snapshot. Never re-bill for the same data.

### 4. Watchlist
- Add any product keyword or advertiser. A background scheduler (APScheduler) re-checks daily at a time I choose.
- Trend charts: active ad count, new creatives per day, and advertiser count over time. A rising ad count means an advertiser is **scaling**, the strongest signal there is.
- Simple alerts in the app (and optionally Windows toast) when a watched product's active ads jump ±30%.

### 5. Compare
- Pick 2–4 products and see the score breakdowns side by side.

### 6. Export
- CSV of ads, plus a one-page PDF/PNG product report I can share.

## Tech stack (defaults; challenge them in brainstorming if you have a better idea)

- **Backend:** Python 3.12, `uv`, FastAPI, Playwright (Chromium), SQLModel + SQLite (`data/adscout.db`), APScheduler, Pydantic models, pytest.
- **Frontend:** Vite + React + TypeScript, Tailwind, shadcn/ui, Recharts (or visx), TanStack Query. The FastAPI server serves the built frontend so it runs as **one command**.
- **Run:** `uv run adscout` opens `http://localhost:8787`. Windows 11 is my primary OS; it must also work on macOS/Linux.
- Live scrape progress over Server-Sent Events.

## Design direction

- Data-dense, **trading-terminal feel**: dark-first with a proper light mode, monospace numbers, tight grids, green/amber/red pass-fail chips on the score breakdown.
- It should look like a product people would pay for, not a template. No generic purple gradients, no emoji-heavy UI.
- Fully usable at 1366px wide; the gallery adapts down to tablet width.
- Empty states that teach ("Try 'posture corrector' in US").

## Milestones (each ends with something I can run and see)

- **M1 – Scraper core:** CLI `uv run adscout scrape "posture corrector" --country US` → normalised ads in SQLite. Parser tests run against recorded fixtures (no live network in tests).
- **M2 – Scoring:** Winner Score + breakdown, unit-tested against fixtures with known expected values.
- **M3 – GUI v1:** Search → live progress → product report (score, timeline, gallery, advertisers).
- **M4 – Claude analysis:** angle clusters + verdict on the report, both backends, cached.
- **M5 – Watchlist + scheduler + trends + compare.**
- **M6 – Ship it public:** README with hero screenshot + 20s demo GIF, an architecture diagram (Mermaid), setup in 3 commands, `.env.example`, MIT license, GitHub Actions CI (lint + tests), a gitleaks secrets scan, issue templates, and a **disclaimer** section: "personal research tool, uses only publicly visible Ad Library data, respect Meta's Terms, low-volume by design".

## Quality bar

- Typed throughout (mypy/pyright on the backend, strict TS on the frontend). Ruff + ESLint + Prettier.
- Scraper isolated behind the `Source` interface. When Meta changes their page, only one module breaks, and the fixture tests show exactly what changed.
- All dates stored in UTC; displayed in local time (configurable, default Australia/Melbourne).
- Graceful failure: if scraping breaks, the UI says what happened and how to fix it (e.g. "Meta asked for login. Click 'Open browser' to sign in").
- Before claiming any milestone is done, run the tests and the app and show me evidence (`superpowers:verification-before-completion`).

## Out of scope (for now)

TikTok Creative Center, Google Ads Transparency, Shopify store revenue estimates, multi-user/cloud hosting, and payments. Keep the `Source` interface ready for TikTok as a future source.

Start now with brainstorming. Your first question should be about the thing you're least sure of.
