# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub security advisories:
<https://github.com/SmitHunter/adscout/security/advisories/new>

Do not open a public issue for a vulnerability, and do not include access tokens, API keys
or cookies in any report. This is a personal project, so response times are best effort.

## Design notes

AdScout is a local-only tool. The web UI and API are meant to be used on localhost, there are
no accounts, and data (SQLite database, media, browser profile) stays in the local `data/`
folder. Optional secrets (`META_ACCESS_TOKEN`, `ANTHROPIC_API_KEY`) are read from your own
`.env`, which is git-ignored. Do not expose the server to an untrusted network.
