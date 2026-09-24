from pathlib import Path
from typing import Optional, Tuple
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRAPER_", env_file=".env", extra="ignore")

    # Asymmetric concurrency: HTTP high, browser = 1 on 8 GB
    max_browser_concurrency: int = Field(default=1, ge=1, le=2)
    max_http_concurrency: int = Field(default=16, ge=1, le=32)

    # Politeness
    per_domain_delay_range: Tuple[float, float] = (3.0, 8.0)
    default_domain_budget: int = 25
    domain_budget_window_seconds: int = 86400  # budget resets daily, not forever
    respect_robots: bool = True

    # Timeouts
    request_timeout: int = 20
    browser_timeout: int = 45

    # Browser lifecycle
    browser_recycle_every_pages: int = 12
    browser_recycle_every_seconds: int = 300

    # Retry
    max_attempts_per_url: int = 2
    max_soft_retries: int = 2

    # Security: max redirect hops the fast path will follow (each hop is
    # re-validated against the SSRF guard before being followed)
    max_redirects: int = 5

    # Paths
    data_dir: Path = Path("data")
    state_db_path: Path = Path("data/state.db")
    cache_dir: Path = Path("data/cache")
    output_dir: Path = Path("data/output")
    failed_log_path: Path = Path("data/failed_urls.txt")
    storage_state_dir: Path = Path("data/storage_state")
    profile_dir: Path = Path("data/profiles")
    summary_path: Path = Path("data/run_summary.json")
    # Local Camoufox cache (avoid re-fetch / GitHub rate limits)
    camoufox_cache_dir: Path = Path.home() / ".cache" / "camoufox"

    # Crawl behaviour
    max_depth: int = 1
    max_pages: int = 0
    same_domain_only: bool = True
    enable_sitemap: bool = True
    enable_cache: bool = True
    enable_change_detection: bool = True
    sitemap_only: bool = False

    # Free-tier anti-bot
    research_mode: bool = True
    escalate_on_soft_block: bool = True
    enable_warmup: bool = True
    enable_storage_state: bool = True
    hard_captcha_only_abort: bool = True
    skip_hard_domains: bool = True
    domain_cooldown_failures: int = 3
    domain_cooldown_seconds: int = 3600

    # Browser engine + presets: fast | stealth
    browser_engine: str = "camoufox"
    camoufox_preset: str = "fast"  # fast | stealth
    camoufox_headless: str | bool = True  # True | False | "virtual"
    camoufox_os: str = "linux"  # windows | macos | linux
    camoufox_locale: str = "en-US"
    camoufox_window_width: int = 1280
    camoufox_window_height: int = 720

    # Proxy + geoip (default OFF — enable via env)
    proxy_server: Optional[str] = None  # e.g. http://host:port
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    enable_geoip: bool = False  # set True when using proxy

    # Only raise browser concurrency if host RAM is large
    min_ram_gb_for_parallel_browser: int = 16

    # Stealth profile (playwright fallback)
    locale: str = "en-US"
    timezone_id: str = "Europe/Berlin"
    viewport_width: int = 1366
    viewport_height: int = 768

    # Phase 1 API
    api_host: str = "0.0.0.0"
    api_port: int = 8080


settings = Settings()
