FROM python:3.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEBIAN_FRONTEND=noninteractive \
    HOME=/home/scraper

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    xvfb ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake browser binaries at build time (avoids GitHub rate limits at runtime)
RUN playwright install chromium || true
RUN python -m camoufox fetch || echo "WARN: camoufox fetch failed at build; mount ~/.cache/camoufox at runtime"

# .dockerignore excludes data/, __pycache__, .git, and other runtime/dev
# files, so COPY . . cannot pull crawl output, cookies, or logs into the
# image — see .dockerignore.
COPY . .
RUN mkdir -p /app/data/output /app/data/cache /app/data/storage_state /app/data/profiles /app/data/jobs

# Run as a non-root user. Chromium still needs --no-sandbox in most
# container runtimes (no user namespace for the sandbox setuid helper), but
# not running as root limits what a compromised browser process can reach
# on the host filesystem.
RUN useradd --create-home --uid 1000 scraper \
    && chown -R scraper:scraper /app /ms-playwright $HOME
USER scraper

# Optional: copy pre-fetched host cache if provided as build context volume
# docker build --build-arg ... or mount -v $HOME/.cache/camoufox:/root/.cache/camoufox

ENV SCRAPER_MAX_HTTP_CONCURRENCY=16 \
    SCRAPER_MAX_BROWSER_CONCURRENCY=1 \
    SCRAPER_CAMOUFOX_PRESET=fast \
    SCRAPER_BROWSER_ENGINE=camoufox

EXPOSE 8080
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8080"]
