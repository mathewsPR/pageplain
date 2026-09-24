# pageplain v1.0.0-rc.2

Self-hosted web knowledge gateway for research and LLM agents (MCP + REST).

## What v1.0 includes

- Fast path: `curl_cffi` TLS impersonation
- Slow path: Camoufox (`fast` / `stealth` presets) + Playwright fallback
- Asymmetric concurrency (HTTP high, browser = 1 if RAM &lt; 16 GB)
- Durable jobs, per-job isolation, zip export
- Domain policy (allow/deny/hard skip)
- Cache-first scrape, search, change detection (`get_diff`)
- MCP server + FastAPI + admin UI
- Usage metrics (cache hits, approx tokens)

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/ensure_browsers.sh    # Camoufox + Chromium once
```

## Smoke test

```bash
python scripts/smoke_test.py           # offline corners + live network
python scripts/smoke_test.py --offline-only
pytest tests/ -q
```

## Run

```bash
# CLI
python main.py https://example.com --max-pages 5

# API + admin UI
uvicorn api_server:app --host 0.0.0.0 --port 8080

# MCP (stdio)
python mcp_server.py
```

## Environment (common)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPER_CAMOUFOX_PRESET` | `fast` | `fast` or `stealth` |
| `SCRAPER_MAX_HTTP_CONCURRENCY` | `16` | HTTP parallelism |
| `SCRAPER_MAX_BROWSER_CONCURRENCY` | `1` | Browser tabs (capped on low RAM) |
| `SCRAPER_SKIP_HARD_DOMAINS` | `true` | Skip Indeed/LinkedIn-class |
| `SCRAPER_PROXY_SERVER` | unset | Optional HTTP proxy |
| `SCRAPER_ENABLE_GEOIP` | `false` | Use with proxy |

## Honesty / limits

- Open and lightly protected sites: good success rate
- Hard anti-bot (Indeed, etc.): skipped or CAPTCHA — needs residential proxies
- Not a Firecrawl replacement for protected sites on $0 budget

## Docker

```bash
docker build -t pageplain:1.0.0-rc.2 .
docker run --rm -p 8080:8080 \
  -v "$HOME/.cache/camoufox:/root/.cache/camoufox" \
  -v "$PWD/data:/app/data" \
  pageplain:1.0.0-rc.2
```

## MCP client snippet

```json
{
  "mcpServers": {
    "pageplain": {
      "command": "python",
      "args": ["/absolute/path/to/scraper/mcp_server.py"],
      "env": {
        "SCRAPER_DATA_DIR": "/absolute/path/to/scraper/data",
        "SCRAPER_CAMOUFOX_PRESET": "fast"
      }
    }
  }
}
```
