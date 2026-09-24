# Pageplain

**v1.0.0-rc.1** — Self-hosted web knowledge gateway for research and AI agents.

Turn URLs into clean markdown with cache-first fetches, durable jobs, domain policy, MCP, and REST — on your own machine, with **$0 per page** after infra.

> **Honest limits:** Strong on open and lightly protected sites. Hard anti-bot targets (e.g. Indeed) are **skipped by default**. Residential proxies are optional and off by default.

## Features

- **Fast path:** `curl_cffi` (browser-like TLS)
- **Slow path:** Camoufox presets (`fast` / `stealth`) + Playwright fallback
- **Asymmetric concurrency:** many HTTP workers, browser concurrency 1 on &lt; 16 GB RAM
- **Jobs:** per-job directories, resume-friendly state, zip export
- **Policy:** allowlist / denylist / hard-domain skip
- **Knowledge:** disk cache, full-text search over crawled pages, `get_diff`
- **Agents:** MCP server + FastAPI + simple admin UI
- **Metrics:** cache hits and approximate token savings

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/ensure_browsers.sh          # Camoufox + Chromium once

# CLI
python main.py https://example.com --max-pages 5

# API + admin UI → http://localhost:8080
uvicorn api_server:app --host 0.0.0.0 --port 8080

# MCP (stdio) — point Cursor/Claude at mcp_server.py
python mcp_server.py
```

### Smoke & eval

```bash
pytest tests/ -q
python scripts/smoke_test.py
python scripts/eval_benchmark.py --out data/eval_report.json
```

## Configuration (env)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPER_CAMOUFOX_PRESET` | `fast` | `fast` or `stealth` |
| `SCRAPER_MAX_HTTP_CONCURRENCY` | `16` | HTTP parallelism |
| `SCRAPER_MAX_BROWSER_CONCURRENCY` | `1` | Browser tabs (capped on low RAM) |
| `SCRAPER_SKIP_HARD_DOMAINS` | `true` | Skip Indeed/LinkedIn-class hosts |
| `SCRAPER_PROXY_SERVER` | unset | Optional HTTP proxy |
| `SCRAPER_ENABLE_GEOIP` | `false` | Use with proxy |

See [RELEASE.md](RELEASE.md) for Docker, MCP client config, and full notes.

## Docker

```bash
docker compose up --build
# or
docker build -t pageplain:1.0.0-rc.1 .
docker run --rm -p 8080:8080 \
  -v "$HOME/.cache/camoufox:/root/.cache/camoufox" \
  -v "$PWD/data:/app/data" \
  pageplain:1.0.0-rc.1
```

## MCP snippet

```json
{
  "mcpServers": {
    "pageplain": {
      "command": "python",
      "args": ["/absolute/path/to/pageplain/mcp_server.py"],
      "env": {
        "SCRAPER_DATA_DIR": "/absolute/path/to/pageplain/data",
        "SCRAPER_CAMOUFOX_PRESET": "fast"
      }
    }
  }
}
```

## Layout

```
pageplain/
  main.py            CLI crawler
  mcp_server.py      MCP tools
  api_server.py      REST + admin UI
  service.py         Shared scrape/job/cache layer
  browser_pool.py    Camoufox / Playwright
  router.py          Fast → slow path
  scripts/           ensure_browsers, smoke, eval
  tests/
```

## Version

See [VERSION](VERSION) and [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Respect site terms of service and `robots.txt`. This software does not guarantee access to protected sites. Use responsibly.
