# Changelog

All notable changes to AdScout are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-18

The first public release.

### Added

- Search the public Meta Ad Library by keyword, country, active status and media type,
  with live progress and cancel. Searches run in AdScout's own Chromium profile, logged out
  by default, one at a time, with random scroll delays and a result cap. Every search is
  saved as a snapshot.
- Optional EU and UK keyword searches through the official Ad Library API
  (`META_ACCESS_TOKEN`).
- The Winner Score: Longevity, Continuity, Creative volume and Angle diversity, each shown
  as a ruler with its fail, pass and full-marks marks. The headline scores the strongest
  advertiser; Market demand pools every advertiser, with a saturation label.
- Relevance filtering with per-ad and per-advertiser overrides, and creative grouping by
  Meta's collation, copy and media, and perceptual image hashes.
- Claude angle analysis through Claude Code or the Anthropic API: angles, hooks, offers,
  seasonality, a verdict and off-topic flags. Cached per snapshot and reused across daily
  checks.
- Creative gallery with locally downloaded media, a launch timeline and an advertiser table.
- Watchlist with a daily check, catch-up after the app was closed, trend charts, ±30%
  alerts and optional desktop notifications (`uv sync --extra notify`).
- Compare 2 to 4 snapshots side by side.
- Export as CSV, a one-page A4 PDF or a 1600 px PNG.
- `adscout demo`: explore the app with invented data, no network calls.
- A command line for every core task: `scrape`, `report`, `analyze`, `exclude`, `media`,
  `browser`, `snapshots` and `watch`.
- CI on Ubuntu, Windows and macOS, a gitleaks secrets scan, issue and pull request
  templates.

[0.1.0]: https://github.com/SmitHunter/adscout/releases/tag/v0.1.0
