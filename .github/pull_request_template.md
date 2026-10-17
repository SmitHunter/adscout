## What and why

<!-- Short description of the change and the reason for it. -->

## Checks

- [ ] `uv run ruff check .` and `uv run ruff format --check src tests`
- [ ] `uv run mypy src tests`
- [ ] `uv run pytest -q`
- [ ] Web changes: `npm --prefix web run lint`, `format:check`, `typecheck`, `test -- --run`, `build`
- [ ] API changes: `uv run python scripts/dump_openapi.py` and committed `web/openapi.json`
- [ ] No scraping or network access in tests; no tokens, cookies or personal data committed
