# Contributing to AdScout

Thanks for helping out. AdScout is a small local tool, so keep changes focused.

## Setup

Prerequisites: Python 3.12 via [uv](https://docs.astral.sh/uv/), Node 22.22+.

```
uv sync
npm --prefix web ci && npm --prefix web run build
uv run playwright install chromium   # only needed for live searches and the browser test
uv run adscout demo                  # explore with sample data
```

For frontend work run `npm --prefix web run dev` alongside `uv run adscout`.

## Checks to run before opening a PR

Python:

```
uv run ruff check .
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest -q
```

Web:

```
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web run typecheck
npm --prefix web test -- --run
npm --prefix web run build
```

If you change an API route or schema, regenerate the contract with
`uv run python scripts/dump_openapi.py` and `npm --prefix web run gen:api`, and commit
`web/openapi.json` (CI fails on drift).

## Tests must not scrape

Tests never touch the live Meta Ad Library or any other network. Use the recorded
fixtures in `tests/fixtures/`, fake the driver, or mock HTTP with `respx`. Do not record
new fixtures that contain personal data, and never commit tokens or cookies.

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `chore:`, `refactor:`, with an optional scope, e.g. `fix(analysis): ...`.
Keep commits small and the subject line under about 72 characters.

## Scope

AdScout is low-volume by design. Changes that add bulk scraping, bypass logins or CAPTCHAs,
or otherwise go against Meta's Terms will not be accepted.
