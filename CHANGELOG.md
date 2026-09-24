# Changelog

All notable changes to **Pageplain** are documented here.

## [1.0.0-rc.2] — 2026-09-24

### Security fixes
- **SSRF / local-file read (critical):** requests could be sent to `file://`,
  loopback, link-local, and other private/internal addresses (e.g.
  `169.254.169.254` cloud metadata endpoints), including via redirects.
  Added a scheme + public-IP guard (`security.py`) enforced before every
  fetch — fast path, browser path, warm-up, redirects, and robots.txt.
- **Policy bypass:** domain allow/deny and hard-domain checks matched on
  `netloc` (includes port and userinfo), so `example.com:443` or
  `user@example.com` slipped past a rule written for `example.com`. Now
  matched on the parsed hostname everywhere a security decision is made.
- **Auth fails open on a corrupt key store:** a corrupted `api_keys.json`
  silently made the API open to everyone. Now fails closed. The bootstrap
  key is written to a 0600 file instead of only stdout, so it no longer
  lands in container/orchestrator logs by default.
- **Stored XSS in the admin UI:** job names were interpolated into
  `innerHTML` unescaped. Rewrote the job table to build DOM nodes with
  `textContent`. The export link also didn't send the auth header (plain
  `<a href>`); replaced with an authenticated fetch + blob download.
- Removed `--ignore-certificate-errors` from the browser launch args (was
  silently accepting invalid TLS certs on every site). Container now runs
  as a non-root user. Added `.dockerignore` so `COPY . .` cannot pull
  runtime data, logs, or secrets into the image.

### Correctness fixes
- `force_refresh` / `get_diff` never actually re-fetched — the router
  ignored the flag and always served the cache. Now threaded through
  `service` → `crawler` → `router`.
- Per-domain page budget was double-counted, locking a domain out
  permanently after roughly half the configured budget. Fixed the
  double-count and added a daily reset window instead of a lifetime cap.
- `state.claim_next`'s lost-update guard checked `conn.total_changes` (a
  lifetime counter, never 0) instead of the UPDATE's own row count, so it
  never actually caught a race between two workers claiming the same URL.

### Added
- `security.py` with test coverage (`tests/test_security.py`).
- `tests/test_regressions.py` covering the cache-bypass and budget fixes.
- `SECURITY.md` with a private disclosure process.
- `.dockerignore`.

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
