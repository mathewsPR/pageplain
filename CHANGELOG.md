# Changelog

All notable changes to **Pageplain** are documented here.

## [1.0.0-rc.1] — 2026-09-24

### Added
- Fast path (`curl_cffi`) + Camoufox (`fast` / `stealth` presets) + Playwright fallback
- Asymmetric concurrency (high HTTP, browser concurrency 1 on RAM &lt; 16 GB)
- Durable jobs with per-job isolation and zip export
- Domain policy (allowlist / denylist / hard-domain skip)
- Content cache, knowledge search, change detection (`get_diff`)
- MCP server (`mcp_server.py`) and REST API + admin UI (`api_server.py`)
- Usage metrics (cache hits, approx tokens)
- Optional proxy + geoip env hooks (default off)
- Smoke test, corner-case tests, open-site eval harness

### Limits
- Hard anti-bot sites (e.g. Indeed) are skipped by default on free tier
- Not a substitute for residential proxies on protected targets

### Docs
- README, RELEASE.md, Docker + compose, ensure_browsers.sh
